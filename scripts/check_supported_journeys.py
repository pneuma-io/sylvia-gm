"""Run the deterministic change gate; installed-candidate acceptance is separate.

Only external providers are scripted in composed journeys. A missing dependency, empty
collection, skipped required check or failed process is a failed gate, never a pass.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON_JOURNEYS = {
    "launch-lite-compatibility": [
        "tests/test_launch_lite_safety.py",
        "tests/test_runtime_payload_cache.py",
        "tests/test_retained_117.py",
        "tests/test_admitted_fixture_isolation.py",
        "tests/test_interrupted_package_staging.py",
        "tests/test_reusable_participants.py",
        "tests/test_srd_lite_character_compatibility.py",
        "tests/test_srd_lite_foundation.py",
        "tests/test_srd_lite_launch_direct.py",
        "tests/test_srd_lite_healing.py",
        "tests/test_srd_lite_attacks.py",
        "tests/test_srd_lite_true_strike_progression.py",
        "tests/test_srd_lite_areas.py",
    ],
    "lite-admitted-qa": [
        "tests/test_qa_lite_death_range.py",
        "tests/test_qa_lite_victory.py",
        "tests/test_qa_lite_victory_boundaries.py",
        "tests/test_qa_lite_physical_range.py",
    ],
    "campaign-play": [
        "tests/test_action_sequences.py",
        "tests/test_action_sequence_provider.py",
        "tests/test_td_native_panel_repairs.py",
        "tests/test_fc_campaign_journey.py",
        "tests/test_fc_party_combat.py",
        "tests/test_fc_campaign_progress.py",
        "tests/test_migrated_plan_lifecycle.py",
        "tests/test_no_rules_interview.py",
    ],
    "builder-and-admission": [
        "tests/test_character_card_import_lifecycle.py",
        "tests/test_journey_pool.py",
        "tests/test_rich_character_profiles.py",
        "tests/test_rich_profile_tools.py",
        "tests/test_character_profiles.py",
        "tests/test_builder_character_profiles.py",
        "tests/test_builder_tool_budget.py",
        "tests/test_character_creation_contract.py",
        "tests/test_character_rules.py",
        "tests/test_starting_character_choices.py",
        "tests/test_shipped_lite_choice_compatibility.py",
        "tests/test_builder_character_reuse.py",
        "tests/test_required_character_completion.py",
        "tests/test_campaign_library_selection.py",
        "tests/test_alpha_f6g_builder_package_journey.py::"
        "test_ordinary_builder_publishes_candidate_then_neutral_runtime_plays_and_reopens",
    ],
    "mechanics-and-state": [
        "tests/test_srd_creature_preparation.py",
        "tests/test_srd_ability_checks.py",
        "tests/test_srd_second_wind.py",
        "tests/test_srd_action_budget.py",
        "tests/test_srd_equipment.py",
        "tests/test_saved_weapon_sets.py",
        "tests/test_fc07_supply_reconciliation.py",
        "tests/test_saved_weapon_sets_migration.py",
        "tests/test_equipment_inventory_actions.py",
        "tests/test_srd_opportunity_attacks.py",
        "tests/test_srd_martial_arts.py",
        "tests/test_srd_acrobatic_movement.py",
        "tests/test_srd_falling.py",
        "tests/test_srd_stunning_strike.py",
        "tests/test_srd_save_damage_features.py",
        "tests/test_srd_wholeness_of_body.py",
        "tests/test_srd_self_restoration.py",
        "tests/test_srd_disciplined_survivor.py",
        "tests/test_srd_superior_defense.py",
        "tests/test_srd_attack_budget.py",
        "tests/test_srd_attack_conditions.py",
        "tests/test_srd_combat_result_cards.py",
        "tests/test_srd_unarmed_creatures.py",
        "tests/test_srd_recovery_result_cards.py",
        "tests/test_srd_action_result_cards.py",
        "tests/test_srd_advancement_result_cards.py",
        "tests/test_srd_initiative_cards.py",
        "tests/test_srd_initiative_recovery.py",
        "tests/test_srd_lifecycle_result_cards.py",
        "tests/test_srd_spell_result_cards.py",
        "tests/test_srd_spell_secondary_effects.py",
        "tests/test_srd_attack_spell_consequences.py",
        "tests/test_srd_condition_spells.py",
        "tests/test_srd_public_spell_casting.py",
        "tests/test_srd_casting_requirements.py",
        "tests/test_srd_spell_grants.py",
        "tests/test_srd_spell_grant_uses.py",
        "tests/test_srd_spell_preparation.py",
        "tests/test_srd_spell_advancement.py",
        "tests/test_srd_spell_materials.py",
        "tests/test_srd_npc_spell_casting.py",
        "tests/test_srd_rest_lifecycle.py",
        "tests/test_runtime_initiative_results.py",
        "tests/test_runtime_template_text.py",
        "tests/test_runtime_entity_snapshots.py",
        "tests/test_runtime_optional_reference_values.py",
        "tests/test_runtime_collections.py",
        "tests/test_canonical_json.py",
        "tests/test_runtime_parameter_defaults.py",
        "tests/test_runtime_choices.py",
        "tests/test_runtime_choice_sequences.py",
        "tests/test_ruleset_design_system.py::"
        "test_opportunity_reaction_executes_from_its_immutable_pending_trigger",
        "tests/test_srd_advancement.py",
        "tests/test_campaign_character_updates.py",
        "tests/test_character_limits.py",
        "tests/test_result_cards.py",
    ],
    "interruption-and-recovery": [
        "tests/test_fc_recovery_composition.py",
        "tests/test_session_history_pages.py",
        "tests/test_alpha_f6e_owner_session_transport.py::"
        "test_owner_manual_retry_reuses_action_and_resettles_campaign_file",
        "tests/test_alpha_f6e_owner_session_transport.py::"
        "test_retry_publication_failure_preserves_checkpoint_across_owner_restart",
        "tests/test_alpha_f6e_owner_session_transport.py::"
        "test_stop_agent_session_cancels_live_builder_provider_and_fences_output",
        "tests/test_alpha_f6e_owner_session_transport.py::"
        "test_builder_steering_reaches_the_next_provider_call_after_a_tool_boundary",
        "tests/test_alpha_f6e_owner_session_transport.py::"
        "test_builder_truncation_drops_incomplete_prose_without_automatic_retry",
        "tests/test_alpha_f5_campaign_lifecycle.py::"
        "test_probe_10_backup_database_produces_coherent_copy",
        "tests/test_alpha_f5_campaign_lifecycle.py::"
        "test_probe_11_export_produces_deterministic_self_contained_archive",
        "tests/test_alpha_f5_campaign_lifecycle.py::"
        "test_probe_12_recovery_after_purge_restores_original_id",
        "tests/test_alpha_f5_campaign_lifecycle.py::test_probe_13_recovery_collision_blocks",
    ],
    "provider-contracts": [
        "tests/test_provider_session_transport_projection.py",
        "tests/test_required_ruleset_decisions.py",
        "tests/test_gm_model_narration_blocks.py",
        "tests/test_fc_provider_routes.py",
        "tests/test_provider_continuation_metadata.py",
        "tests/test_private_provider_records.py",
        "tests/test_alpha_f4_provider_session_adapter.py::"
        "test_only_one_state_changing_mechanic_executes_per_catalog_snapshot",
        "tests/test_alpha_f4_provider_session_adapter.py::"
        "test_persisted_mixed_turn_recovers_exact_rejected_blocks_without_replaying_action",
    ],
    "bundled-content": [
        "tests/test_plan_snapshot_cache.py",
        "tests/test_bundled_exact_reuse.py",
        "tests/test_bundled_campaign_collection.py",
        "tests/test_srd_generation_boundaries.py",
    ],
    "release-integrity": [
        "tests/test_protected_provider_settings.py",
        "tests/test_portable_identity.py",
        "tests/test_fc01_release_exclusions.py",
        "tests/test_bundled_licence_notices.py",
        "tests/test_sylvia_gm_identity.py",
        "tests/test_alpha_09_release_artifact.py",
        "tests/test_supported_journey_gate.py",
        "tests/test_release_identity_gate.py",
        "tests/test_portable_release_recipe.py",
        "tests/test_build_workspace.py",
        "tests/test_ci_selection.py",
        "tests/test_ci_diagnostics.py",
        "tests/test_journey_progress.py",
    ],
    "generic-procedures": [
        "tests/test_runtime_procedure_failures.py",
        "tests/test_ruleset_modifier_evaluation.py",
        "tests/test_runtime_nested_rejections.py",
    ],
}
UI_JOURNEY = [
    "tests/frontend_character_equipment.test.cjs",
    "tests/frontend_character_selection.test.cjs",
    "tests/frontend_character_inspection.browser.cjs",
    "tests/frontend_game_controls.test.cjs",
    "tests/frontend_saved_choice_commit.test.cjs",
    "tests/frontend_map_initial_camera.test.cjs",
    "tests/frontend_map_initial_camera.browser.cjs",
    "tests/frontend_map_camera.browser.cjs",
    "tests/frontend_character_card_import.browser.cjs",
    "tests/frontend_campaign_endings.test.cjs",
    "tests/frontend_act_markers.test.cjs",
    "tests/frontend_agent_activity.test.cjs",
    "tests/frontend_saved_sessions.test.cjs",
    "tests/frontend_result_card_actions.test.cjs",
    "tests/frontend_result_migration.test.cjs",
    "tests/frontend_player_actions.test.cjs",
    "tests/frontend_action_sequence_bridge.test.cjs",
    "tests/frontend_default_queue.test.cjs",
    "tests/frontend_mechanics_choices.test.cjs",
    "tests/frontend_party_panel_actions.test.cjs",
    "tests/frontend_result_migration.browser.cjs",
    "tests/frontend_shell.browser.cjs",
    "tests/frontend_saved_equipment.browser.cjs",
]


def source_identity() -> dict[str, object]:
    """Bind evidence to every relevant source/test/input byte, including untracked fixes."""
    files = {}
    for folder in (
        "src",
        "scripts",
        "tests",
        "rulesets",
        "campaign-settings",
        "campaign-plans",
        "config",
        "docs",
        "licenses",
        ".github",
    ):
        for path in sorted((ROOT / folder).rglob("*")):
            if path.is_file() and not any(
                part in ("__pycache__", "node_modules", ".pytest_cache") for part in path.parts
            ):
                files[path.relative_to(ROOT).as_posix()] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
    for path in sorted((ROOT / "src-tauri").rglob("*")):
        if path.is_file() and not any(
            part in ("target", "binaries", "node_modules", "__pycache__")
            for part in path.relative_to(ROOT).parts
        ):
            files[path.relative_to(ROOT).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    for name in (
        "pyproject.toml",
        "uv.lock",
        "README.md",
        "AGENTS.md",
        "CONTEXT.md",
        "LICENSE",
        "COPYRIGHT",
        "LICENSING-STATUS.md",
        "SYLVIA-ADDITIONAL-PERMISSIONS.md",
        "dependency-notices.txt",
    ):
        if (ROOT / name).is_file():
            files[name] = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
    head = None
    if (ROOT / ".git").exists():
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    digest = hashlib.sha256(
        json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    expected = os.environ.get("SYLVIA_EXPECTED_REVISION")
    if expected and head != expected:
        raise ValueError("Checked-out revision differs from required current workflow revision")
    return {"sha256": digest, "head": head, "files": files}


def _remaining_timeout(deadline):
    return None if deadline is None else max(0.001, deadline - time.monotonic())


def _parallel_pytest_cases(name, destination, testdata, environment, nodeids, count, deadline=None):
    """Run every collected case once, with separate mutable data and an optional CI deadline."""
    assignments = [nodeids[index::count] for index in range(count)]

    def run_shard(index, selected):
        prefix = destination / f"{name}-shard-{index}"
        xml = prefix.with_suffix(".xml")
        log = prefix.with_suffix(".log")
        data = testdata.with_name(testdata.name + f"-shard-{index}")
        if data.exists() or not data.resolve().is_relative_to(testdata.parent.resolve()):
            raise ValueError("Parallel test data must be a fresh contained directory")
        args = [
            "-s",
            "-o",
            f"cache_dir={destination / 'pytest-cache' / str(index)}",
            f"--junitxml={xml}",
            f"--basetemp={data}",
            *selected,
        ]
        argument_file = prefix.with_suffix(".arguments.json")
        argument_file.write_text(json.dumps(args), encoding="utf-8")
        # An argument file avoids Windows command-line limits without shell expansion.
        launcher = (
            "import json,sys,pytest; "
            "raise SystemExit(pytest.main(json.load(open(sys.argv[1],encoding='utf-8'))))"
        )
        try:
            with log.open("w", encoding="utf-8") as stream:
                result = subprocess.run(
                    [sys.executable, "-c", launcher, str(argument_file)],
                    cwd=ROOT,
                    env=environment,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=_remaining_timeout(deadline),
                    check=False,
                )
            return result.returncode == 0, xml, log
        except (OSError, subprocess.TimeoutExpired) as error:
            with log.open("a", encoding="utf-8") as stream:
                stream.write(f"\n{type(error).__name__}: {error}")
            return False, xml, log

    with ThreadPoolExecutor(max_workers=count) as executor:
        futures = [
            executor.submit(run_shard, index, selected)
            for index, selected in enumerate(assignments)
            if selected
        ]
        results = [future.result() for future in futures]
    merged = ET.Element("testsuites")
    passed = bool(results)
    with (destination / f"{name}.log").open("w", encoding="utf-8") as stream:
        for successful, xml, log in results:
            passed &= successful
            stream.write(log.read_text(encoding="utf-8"))
            if not xml.is_file():
                passed = False
                continue
            try:
                report = ET.parse(xml).getroot()
                for suite in report.findall("testsuite"):
                    merged.append(suite)
            except ET.ParseError as error:
                stream.write(f"\nMalformed shard report: {error}")
                passed = False
    ET.ElementTree(merged).write(destination / f"{name}.xml", encoding="utf-8")
    return passed


def _run(
    name: str, destination: Path, *, case_workers: int = 1, deadline=None
) -> tuple[bool, float]:
    """Keep complete synthetic test diagnostics and reject incomplete required coverage."""
    xml = destination / f"{name}.xml"
    testdata = destination / "attempts" / name
    if name in ("lite-admitted-qa", "launch-lite-compatibility", "builder-and-admission"):
        # The lossless fixture archival helper requires this owned QA parent.
        # A fresh diagnostics destination names a distinct contained test run.
        run_id = hashlib.sha256(str(destination.resolve()).encode()).hexdigest()[:12]
        owned_attempts = (destination / "attempts").resolve()
        testdata = owned_attempts / ("gate-" + run_id) / name
        if not testdata.resolve().is_relative_to(owned_attempts) or testdata.exists():
            raise ValueError("Admitted QA test directory is not a fresh contained target")
        testdata.parent.mkdir(parents=True, exist_ok=True)
    testdata.parent.mkdir(parents=True, exist_ok=True)
    if name == "ui-controls":
        node = shutil.which("node")
        if node is None:
            print("FAIL ui-controls: Node.js is required", flush=True)
            (destination / f"{name}.log").write_text("Node.js is required", encoding="utf-8")
            return False, 0.0
        # Concurrent Edge startups timed out while the Python pool was busy.
        # Keep the original per-test limits and all cases; bound browser starts.
        command = [node, "--test", "--test-concurrency=2", "--test-reporter=tap", *UI_JOURNEY]
    else:
        command = [
            sys.executable,
            "-m",
            "pytest",
            "-s",
            "-o",
            f"cache_dir={destination / 'pytest-cache' / name}",
            f"--junitxml={xml}",
            f"--basetemp={testdata}",
            *PYTHON_JOURNEYS[name],
        ]
        if os.environ.get("SYLVIA_RETAIN_SUCCESSFUL_FIXTURES") == "0":
            command.extend(["-o", "tmp_path_retention_policy=failed"])
    started = time.monotonic()
    print(f"RUN  {name}", flush=True)
    child_environment = dict(os.environ)
    child_environment["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT / "src"), str(ROOT), child_environment.get("PYTHONPATH", "")]
    )
    child_environment.pop("PYTEST_ADDOPTS", None)
    child_environment["PYTHONUNBUFFERED"] = "1"
    child_environment["SYLVIA_QA_FIXTURE_ROOT"] = str(destination.resolve())
    # Keep archive spools and browser profiles in this run's owned data volume.
    temporary = destination / "temporary" / name
    temporary.mkdir(parents=True, exist_ok=True)
    for variable in ("TEMP", "TMP", "TMPDIR"):
        child_environment[variable] = str(temporary.resolve())
    if name == "ui-controls":
        child_environment["SYLVIA_TEST_PYTHON"] = sys.executable
    if name in ("lite-admitted-qa", "launch-lite-compatibility", "builder-and-admission"):
        # This exact input is included in source_identity, regardless of ambient
        # candidate overrides. Missing or unreadable bytes fail test execution.
        child_environment.setdefault(
            "SRD_LITE_CANDIDATE",
            str(ROOT / "tests/fixtures/SRD-Lite-117-QA-Legacy-Contract-Clean.sylvia-gm-package"),
        )
    expected_cases = set()
    collected_nodeids = []
    try:
        if name != "ui-controls":
            collection = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "--collect-only",
                    "-q",
                    "-o",
                    "addopts=",
                    *PYTHON_JOURNEYS[name],
                ],
                cwd=ROOT,
                env=child_environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_remaining_timeout(deadline),
                check=False,
            )
            for line in collection.stdout.splitlines():
                if line.startswith("tests/") and "::" in line:
                    collected_nodeids.append(line)
                    path, *parts = line.split("::")
                    expected_cases.add(
                        (
                            path.removesuffix(".py").replace("/", ".")
                            + ("." + ".".join(parts[:-1]) if len(parts) > 1 else ""),
                            parts[-1],
                        )
                    )
            (destination / f"{name}-required-cases.json").write_text(
                json.dumps(sorted(expected_cases), indent=2),
                encoding="utf-8",
            )
            if collection.returncode != 0 or not expected_cases:
                raise ValueError(
                    "Required test collection failed or contained no checks: "
                    + (collection.stdout + collection.stderr)[-4000:]
                )
        # Stream owned diagnostics as checks execute; a timeout must not discard
        # the passed/failed cases and traces that led to it.
        if case_workers > 1 and name != "ui-controls":
            passed = _parallel_pytest_cases(
                name,
                destination,
                testdata,
                child_environment,
                collected_nodeids,
                case_workers,
                deadline,
            )
            output = (destination / f"{name}.log").read_text(encoding="utf-8")
        else:
            with (destination / f"{name}.log").open("w", encoding="utf-8") as stream:
                result = subprocess.run(
                    command,
                    cwd=ROOT,
                    env=child_environment,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=_remaining_timeout(deadline),
                    check=False,
                )
            output = (destination / f"{name}.log").read_text(encoding="utf-8")
            output += (result.stdout or "") + (result.stderr or "")
            passed = result.returncode == 0
        if name == "ui-controls":
            passed &= bool(re.search(r"^# tests [1-9]\d*\s*$", output, re.MULTILINE))
            passed &= not bool(
                re.search(r"^# (cancelled|skipped|todo) [1-9]", output, re.MULTILINE)
            )
        elif xml.exists():
            report = ET.parse(xml).getroot()
            actual_cases = [
                (case.get("classname"), case.get("name")) for case in report.findall(".//testcase")
            ]
            passed &= bool(actual_cases) and len(actual_cases) == len(expected_cases)
            passed &= set(actual_cases) == expected_cases
            passed &= not any(
                report.findall(f".//{tag}") for tag in ("skipped", "failure", "error")
            )
        else:
            passed = False
    except (OSError, subprocess.TimeoutExpired, ET.ParseError, ValueError) as error:
        passed = False
        existing = destination / f"{name}.log"
        output = existing.read_text(encoding="utf-8") if existing.exists() else ""
        output += f"\n{type(error).__name__}: {error}"
    (destination / f"{name}.log").write_text(output, encoding="utf-8")
    elapsed = time.monotonic() - started
    print(f"{'PASS' if passed else 'FAIL'} {name} ({elapsed:.1f}s)", flush=True)
    if not passed:
        print(output[-16000:], flush=True)
    return bool(passed), elapsed


def _run_guarded(
    name: str, destination: Path, *, case_workers: int = 1, deadline=None
) -> tuple[bool, float]:
    """Preserve an early setup failure as required-group evidence rather than lose the summary."""
    started = time.monotonic()
    try:
        return _run(name, destination, case_workers=case_workers, deadline=deadline)
    except (OSError, ValueError, MemoryError) as error:
        log = destination / f"{name}.log"
        prior = log.read_text(encoding="utf-8") if log.exists() else ""
        log.write_text(prior + f"\n{type(error).__name__}: {error}", encoding="utf-8")
        elapsed = time.monotonic() - started
        print(f"FAIL {name} setup ({elapsed:.1f}s): {type(error).__name__}: {error}", flush=True)
        return False, elapsed


def main() -> int:
    """The default is the complete change gate; selections are explicitly partial checks."""
    global PYTHON_JOURNEYS, UI_JOURNEY
    names = [*PYTHON_JOURNEYS, "ui-controls"]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ci",
        action="store_true",
        help=(
            "Use the 1800-second whole-run budget for CI; development runs have no wall-time cutoff"
        ),
    )
    parser.add_argument(
        "--journey",
        action="append",
        choices=names,
        help="Run a selected group for diagnosis; this is not the full gate",
    )
    parser.add_argument(
        "--list", action="store_true", help="List the actual checks without running"
    )
    parser.add_argument(
        "--jobs",
        type=int,
        choices=(1, 2, 3, 4),
        default=2,
        help="Independent groups (default: 2); long groups use bounded isolated case workers",
    )
    parser.add_argument(
        "--case-workers",
        type=int,
        choices=(1, 2, 3, 4),
        default=1,
        help="Workers per long group (default 1); complete runs use jobs times this value",
    )
    parser.add_argument(
        "--scheduler",
        choices=("module", "case"),
        default="case",
        help="Balance measured cases (default); optional module fixture affinity",
    )
    parser.add_argument(
        "--selection-manifest",
        type=Path,
        help="Source-bound affected PR selection; never whole-product acceptance",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Empty destination for synthetic diagnostics, timings and XML reports",
    )
    args = parser.parse_args()
    if args.ci and args.journey:
        parser.error("--ci requires the complete suite")
    if args.selection_manifest and args.journey:
        parser.error("A selection manifest cannot be combined with an ad hoc journey")
    selected = list(dict.fromkeys(args.journey or names))
    if args.list:
        for name in selected:
            print(
                name
                + ":\n  "
                + "\n  ".join(
                    UI_JOURNEY if name == "ui-controls" else PYTHON_JOURNEYS[name],
                )
            )
        return 0
    destination = (args.output_dir or Path(tempfile.mkdtemp(prefix="sylvia-journeys-"))).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        parser.error("--output-dir must be empty; previous results must not mix with this run")
    print(f"Synthetic test diagnostics: {destination}", flush=True)
    (destination / "summary.json").write_text(
        json.dumps(
            {
                "status": "running",
                "passed": False,
                "complete_gate": False,
                "reason": "No completed result yet",
            }
        ),
        encoding="utf-8",
    )
    initial_identity = source_identity()
    (destination / "source-identity.json").write_text(
        json.dumps(initial_identity, indent=2), encoding="utf-8"
    )
    selection = None
    if args.selection_manifest:
        if __package__:
            from .select_checks import load_selection
        else:
            from select_checks import load_selection
        selection = load_selection(
            args.selection_manifest, revision=initial_identity["head"], root=ROOT
        )
        if args.ci and selection["mode"] != "full":
            parser.error("The future post-launch budget requires explicit full qualification")
        PYTHON_JOURNEYS, UI_JOURNEY = selection["python"], selection["ui"]
        selected = [*PYTHON_JOURNEYS, *(["ui-controls"] if UI_JOURNEY else [])]
        (destination / "selection.json").write_text(
            json.dumps(selection, indent=2), encoding="utf-8"
        )
    started = time.monotonic()
    time_budget = 1800 if args.ci else None
    deadline = None if time_budget is None else started + time_budget

    def run_group(name, output, *, case_workers=1):
        return _run_guarded(name, output, case_workers=case_workers, deadline=deadline)

    # Each group runs in its own process and pytest temporary directory; only immutable
    # repository inputs are shared. Keep concurrency bounded on the Windows CI runner.
    if args.journey is None:
        from journey_pool import run_balanced_gate

        results = run_balanced_gate(
            ROOT,
            destination,
            PYTHON_JOURNEYS,
            selected,
            min(10, args.jobs * args.case_workers),
            started,
            run_group,
            deadline=deadline,
            scheduler=args.scheduler,
        )
    else:
        with ThreadPoolExecutor(max_workers=args.jobs) as executor:
            # Start the longest group immediately; short groups must not delay its critical path.
            execution_order = sorted(
                selected,
                key=lambda name: (
                    0 if name == "mechanics-and-state" else 1 if name == "campaign-play" else 2
                ),
            )
            pending = {
                name: executor.submit(
                    run_group,
                    name,
                    destination,
                    case_workers=args.case_workers
                    if name in ("campaign-play", "mechanics-and-state", "launch-lite-compatibility")
                    else 1,
                )
                for name in execution_order
            }
            results = {name: future.result() for name, future in pending.items()}
    elapsed = time.monotonic() - started
    failed = [name for name, (passed, _) in results.items() if not passed]
    missing_groups = sorted(set(selected) - set(results))
    unexpected_groups = sorted(set(results) - set(selected))
    failed.extend(f"missing group: {name}" for name in missing_groups)
    failed.extend(f"unexpected group: {name}" for name in unexpected_groups)
    within_time_budget = time_budget is None or elapsed <= time_budget
    unchanged_source = source_identity() == initial_identity
    if not unchanged_source:
        failed.append("source changed during checks")
    summary = {
        "status": "completed",
        "passed": not failed and within_time_budget,
        "complete_gate": args.journey is None
        and (selection is None or selection["mode"] == "full"),
        "gate_kind": "affected PR"
        if selection and selection["mode"] == "affected"
        else "complete deterministic",
        "missing_groups": missing_groups,
        "unexpected_groups": unexpected_groups,
        "qualification_mode": "ci" if args.ci else "development",
        "time_budget_seconds": time_budget,
        "within_time_budget": within_time_budget,
        "elapsed_seconds": round(elapsed, 2),
        "jobs": args.jobs,
        "case_workers": dict.fromkeys(
            ("mechanics-and-state", "campaign-play", "launch-lite-compatibility"), args.case_workers
        )
        if args.journey
        else {},
        "execution_mode": "selected groups" if args.journey else "shared balanced pool",
        "group_timing_basis": (
            "Explicit wall_seconds or case_worker_seconds; Python case times include "
            "setup/call/teardown, not concurrent group wall time"
        ),
        "scheduler": args.scheduler,
        "total_case_workers": min(10, args.jobs * args.case_workers) if not args.journey else None,
        "groups": {
            name: {
                "passed": passed,
                "wall_seconds"
                if args.journey or name == "ui-controls"
                else "case_worker_seconds": round(seconds, 2),
            }
            for name, (passed, seconds) in results.items()
        },
        "installed_acceptance_run": False,
        "source_sha256": initial_identity["sha256"],
        "revision": initial_identity["head"],
        "unchanged_source": unchanged_source,
    }
    (destination / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    label = (
        "Affected PR checks"
        if selection and selection["mode"] == "affected"
        else "Selected checks"
        if args.journey
        else "CI qualification"
        if args.ci
        else "Development qualification"
    )
    failure_labels = failed + ([] if within_time_budget else ["time budget"])
    print(
        f"{label}: {'FAIL: ' + ', '.join(failure_labels) if failure_labels else 'PASS'}",
        flush=True,
    )
    print(
        f"Test execution: {elapsed:.1f}s; includes process setup, excludes dependency installation"
    )
    if not within_time_budget:
        print(f"FAIL time budget: complete gate exceeds {time_budget}s", flush=True)
    print("Installed app and genuine-provider acceptance: NOT RUN by this command", flush=True)
    return 1 if failed or not within_time_budget else 0


if __name__ == "__main__":
    raise SystemExit(main())
