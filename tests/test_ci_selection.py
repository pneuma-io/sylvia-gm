"""Change selection must stay conservative and explain why work is required."""

from pathlib import Path

import pytest
from scripts.select_checks import select_checks


def choose(paths, **kwargs):
    return select_checks(paths, source="a" * 40, base="b" * 40, **kwargs)


def test_readme_does_not_replay_campaigns_or_compile_owner():
    plan = choose(["README.md"])
    assert plan["mode"] == "affected"
    assert not plan["native"] and not plan["ui"]
    assert "campaign-play" not in plan["python"]
    assert plan["documents"] == ["README.md"]
    assert "tests/test_sylvia_gm_identity.py" in plan["python"]["copy-and-links"]
    assert plan["source"] == "a" * 40 and plan["base"] == "b" * 40
    assert plan["reasons"][0]["path"] == "README.md"


@pytest.mark.parametrize("path", [
    "branding/sprites/tutorial-poses.png",
    "branding/sprites/provenance.json",
    "scripts/extract_tutorial_sprites.py",
])
def test_tutorial_artwork_selects_its_packaging_and_ui_consumers(path):
    plan = choose([path])
    assert not plan["broad_fallback"]
    assert not plan["native"]
    assert plan["python"]["tutorial-assets"] == ["tests/test_tutorial_assets.py"]
    assert "tests/frontend_tutorials.browser.cjs" in plan["ui"]
    assert "campaign-play" not in plan["python"]


@pytest.mark.parametrize(
    "path", ["rulesets/srd-lite/ATTRIBUTION.md", "campaign-settings/severed-lands/ATTRIBUTION.md"]
)
def test_shipped_content_markdown_keeps_package_and_notice_checks(path):
    plan = choose([path])
    assert "bundled-content" in plan["python"]
    assert "release-integrity" in plan["python"]
    assert "copy-and-links" not in plan["python"]


def test_executable_skill_markdown_selects_runtime_dependents():
    plan = choose(["src/sylvia_gm/agent/skills/game-master/SKILL.md"])
    assert "provider-contracts" in plan["python"]
    assert "campaign-play" in plan["python"]
    assert "copy-and-links" not in plan["python"]


def test_unknown_markdown_is_not_assumed_to_be_documentation():
    assert choose(["unknown/operational.md"])["broad_fallback"]


def test_edited_campaign_test_helper_selects_all_plan_lifecycle_variants():
    plan = choose(["tests/test_fc_campaign_journey.py"])
    selectors = [item for values in plan["python"].values() for item in values]
    assert "tests/test_migrated_plan_lifecycle.py" in selectors
    assert "tests/test_fc_party_combat.py" in selectors
    assert "tests/test_fc_campaign_progress.py" in selectors


def test_authored_plan_checks_keep_srd_and_dedicated_riichi_coverage():
    plan = choose(["campaign-plans/riichi-quick-test/plan.json"])
    assert "tests/test_migrated_plan_lifecycle.py" in plan["python"]["campaign-play"]
    assert "tests/test_riichi_quick_test_plan.py" in plan["python"]["campaign-play"]


@pytest.mark.parametrize("module", ["bundled_packages", "bundled_campaign_plans"])
def test_package_materializers_keep_exact_closure_and_release_consumers(module):
    plan = choose([f"src/sylvia_gm/application/{module}.py"])
    selectors = [item for values in plan["python"].values() for item in values]
    assert "tests/test_bundled_exact_reuse.py" in selectors
    assert "tests/test_bundled_campaign_collection.py" in selectors
    assert "tests/test_plan_snapshot_cache.py" in selectors
    assert "tests/test_migrated_plan_lifecycle.py" in selectors
    assert "tests/test_portable_release_recipe.py" in selectors


def test_transitive_test_imports_are_followed_without_executing_them(tmp_path):
    from scripts.select_checks import reverse_test_dependents

    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "helper.py").write_text("raise RuntimeError('never execute imports')")
    (tests / "intermediate.py").write_text("from tests.helper import exported")
    (tests / "test_consumer.py").write_text("from .intermediate import exported")
    affected = reverse_test_dependents(["tests/helper.py"], tmp_path)
    assert affected == {"tests/helper.py", "tests/intermediate.py", "tests/test_consumer.py"}


@pytest.mark.parametrize("paths,complete", [([], True), (["README.md"], False), (None, False)])
def test_missing_empty_or_incomplete_diff_requires_broad_work(paths, complete):
    plan = choose(paths, diff_complete=complete)
    assert plan["broad_fallback"] and plan["native"] and plan["ui"]
    assert "campaign-play" in plan["python"]
    assert plan["python"]["release-integrity"]
    assert plan["mode"] == "affected", "A broad PR check is not release acceptance"


@pytest.mark.parametrize(
    "path", ["unmapped.file", "pyproject.toml", "src/sylvia_gm/domain/authority.py"]
)
def test_unknown_or_cross_cutting_source_is_conservative(path):
    plan = choose([path])
    assert plan["broad_fallback"] and len(plan["python"]) >= 10


@pytest.mark.parametrize(
    "path", ["sylviabench/checks.py", "sylviabench/scenarios.json", "scripts/sylviabench.py"]
)
def test_benchmark_edits_select_their_own_fast_checks_without_product_replay(path):
    plan = choose([path])
    assert not plan["broad_fallback"] and not plan["native"] and not plan["ui"]
    assert set(plan["python"]) == {"benchmark-framework"}
    assert "tests/test_sylviabench_checks.py" in plan["python"]["benchmark-framework"]
    # The real-owner acceptance module takes minutes and stays with explicit --full.
    assert "tests/test_sylviabench_gameplay.py" not in plan["python"]["benchmark-framework"]


def test_frontend_selects_javascript_and_browser_without_heavy_python():
    plan = choose(["src-tauri/frontend/app.js"])
    assert plan["ui"] and any(path.endswith(".browser.cjs") for path in plan["ui"])
    assert any(path.endswith(".test.cjs") for path in plan["ui"])
    assert not plan["native"] and "mechanics-and-state" not in plan["python"]


@pytest.mark.parametrize(
    "path,group",
    [
        ("src/sylvia_gm/provider/_base.py", "provider-contracts"),
        ("src/sylvia_gm/ruleset/runtime/procedure_executor.py", "generic-procedures"),
        ("src/sylvia_gm/application/builder.py", "builder-and-admission"),
        ("rulesets/srd-lite.sylvia-gm-package", "lite-admitted-qa"),
        ("campaign-plans/plan.json", "campaign-play"),
    ],
)
def test_affected_runtime_and_content_keep_security_and_composed_boundaries(path, group):
    plan = choose([path])
    assert group in plan["python"]
    assert "core-invariants" in plan["python"]
    assert "tests/test_private_provider_records.py" in plan["python"]["core-invariants"]
    if path.startswith("src/sylvia_gm/provider/"):
        assert "gm-turn" in plan["python"]
        assert "tests/test_alpha_f4_generic_sessions.py" in plan["python"]["gm-turn"]
    else:
        assert any("recovery" in name for name in plan["python"])


@pytest.mark.parametrize(
    "path", ["src-tauri/src/lib.rs", "src-tauri/Cargo.toml", "scripts/build_windows_prerelease.py"]
)
def test_shell_and_build_changes_select_native_and_release_proof(path):
    plan = choose([path])
    assert plan["native"] and "release-integrity" in plan["python"]


def test_licence_and_policy_edits_select_notice_checks():
    plan = choose(["LICENSE", "docs/launch-compatibility.md"])
    assert "tests/test_bundled_licence_notices.py" in plan["python"]["release-integrity"]
    assert not plan["native"]


@pytest.mark.parametrize(
    "path",
    [
        "scripts/select_checks.py",
        ".github/workflows/supported-journeys.yml",
        "tests/admitted_library_fixture.py",
    ],
)
def test_harness_changes_test_selection_and_fixture_invariants(path):
    plan = choose([path])
    assert "tests/test_ci_selection.py" in plan["python"]["ci-harness"]
    assert "tests/test_journey_pool.py" in plan["python"]["ci-harness"]
    assert "tests/test_admitted_fixture_isolation.py" in plan["python"]["ci-harness"]


def test_full_request_keeps_every_original_group_and_ui():
    from scripts.check_supported_journeys import PYTHON_JOURNEYS, UI_JOURNEY

    plan = choose(["README.md"], full=True)
    assert plan["mode"] == "full" and plan["native"]
    assert plan["python"] == PYTHON_JOURNEYS and plan["ui"] == UI_JOURNEY


def test_combined_selection_has_no_repeated_or_overlapping_selectors():
    plan = choose(["src/sylvia_gm/provider/_base.py", "tests/test_private_provider_records.py"])
    selectors = [item for group in plan["python"].values() for item in group]
    assert len(selectors) == len(set(selectors))
    for item in selectors:
        if "::" in item:
            assert item.split("::")[0] not in selectors


def test_slow_files_are_selected_only_for_explicit_full_qualification():
    from scripts.check_supported_journeys import FULL_ONLY_JOURNEYS, PYTHON_JOURNEYS

    slow = {
        item.split("::")[0]
        for group in FULL_ONLY_JOURNEYS for item in PYTHON_JOURNEYS[group]
    }
    assert slow
    for plan in (choose(["unmapped.file"]), choose([sorted(slow)[0]])):
        selected = {
            item.split("::")[0] for values in plan["python"].values() for item in values
        }
        assert selected and not selected & slow
    full = choose([], full=True)
    assert slow <= {
        item.split("::")[0] for values in full["python"].values() for item in values
    }


def test_browser_fixture_helpers_select_their_python_backend():
    from scripts.check_supported_journeys import FULL_ONLY_JOURNEYS, PYTHON_JOURNEYS
    from scripts.test_inventory import JAVASCRIPT_HARNESSES

    for helper, owner in JAVASCRIPT_HARNESSES.items():
        plan = choose([helper])
        assert helper not in plan["ui"]
        selected = [item for values in plan["python"].values() for item in values]
        full_only = {item for group in FULL_ONLY_JOURNEYS for item in PYTHON_JOURNEYS[group]}
        if owner in full_only:
            assert owner not in selected
            assert any(row["selection"] == "explicit --full only" for row in plan["reasons"])
        else:
            assert owner in selected
        full = choose([helper], full=True)
        assert owner in [item for values in full["python"].values() for item in values]


def test_local_cli_accepts_an_explicit_base_without_a_github_event(tmp_path, monkeypatch):
    import json
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    output = tmp_path / "selection.json"
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    result = subprocess.run(
        [sys.executable, "scripts/select_checks.py", "--base", "HEAD", "--output", str(output)],
        cwd=root, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    plan = json.loads(output.read_text())
    assert plan["diff_complete"] and plan["base"] == plan["source"]
    assert plan["python"]  # An empty diff still cannot silently select zero work.


def test_all_selected_current_tests_exist():
    plan = choose(["unmapped.file"])
    root = Path(__file__).resolve().parents[1]
    assert all(
        (root / item.split("::")[0]).is_file()
        for group in plan["python"].values()
        for item in group
    )
    assert all((root / item).is_file() for item in plan["ui"])


@pytest.mark.parametrize(
    "change", ["revision", "empty", "unsafe", "missing", "full_omits_required", "overlap"]
)
def test_manifest_reader_rejects_stale_missing_unsafe_and_incomplete_work(tmp_path, change):
    import json

    from scripts.select_checks import load_selection

    plan = choose(["README.md"])
    root = Path(__file__).resolve().parents[1]
    if change == "revision":
        plan["source"] = "wrong"
    elif change == "empty":
        plan["python"] = {}
    elif change == "unsafe":
        plan["python"] = {"escape": ["tests/../../outside.py"]}
    elif change == "missing":
        plan["python"] = {"missing": ["tests/test_not_here.py"]}
    elif change == "overlap":
        plan["python"]["duplicate"] = list(plan["python"]["copy-and-links"])
    else:
        plan["mode"] = "full"
    path = tmp_path / "selection.json"
    path.write_text(json.dumps(plan))
    with pytest.raises(ValueError):
        load_selection(path, revision="a" * 40, root=root)


def test_changed_doc_links_reject_missing_local_targets(tmp_path):
    from scripts.check_document_links import check_links

    (tmp_path / "README.md").write_text("[ok](other.md) [web](https://example.test) [bad](lost.md)")
    (tmp_path / "other.md").write_text("existing")
    assert check_links(tmp_path, ["README.md"]) == ["README.md: missing local link lost.md"]


def test_workflow_is_manual_only_and_retains_full_qualification():
    import yaml

    root = Path(__file__).resolve().parents[1]
    workflow = yaml.load(
        (root / ".github/workflows/supported-journeys.yml").read_text(), Loader=yaml.BaseLoader
    )
    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert workflow["on"]["workflow_dispatch"]["inputs"]["qualification"]["options"] == [
        "affected",
        "full",
        "benchmark",
        "browser",
    ]
    assert "native == 'true'" in workflow["jobs"]["rust-tests"]["if"]
    diagnostic = next(
        step
        for step in workflow["jobs"]["deterministic-journeys"]["steps"]
        if step.get("name") == "Retain compact synthetic diagnostics"
    )
    assert diagnostic["with"]["path"].endswith("/retained-diagnostics/")


def test_rust_workflow_exports_validated_base_python_before_unchanged_cargo_tests():
    import yaml

    root = Path(__file__).resolve().parents[1]
    workflow = yaml.load(
        (root / ".github/workflows/supported-journeys.yml").read_text(), Loader=yaml.BaseLoader
    )
    steps = workflow["jobs"]["rust-tests"]["steps"]
    cargo = next(
        index
        for index, step in enumerate(steps)
        if step.get("run")
        == "cargo test --locked --all-targets --manifest-path src-tauri/Cargo.toml"
    )
    setup = steps[cargo - 1]
    run = setup["run"]
    assert '"SYLVIA_GM_TEST_PYTHON=$basePython" >> $env:GITHUB_ENV' in run
    assert setup["shell"] == "pwsh"
    assert "uv run --no-sync --no-python-downloads python" in run
    assert "--no-project" not in run and "--managed-python" not in run
    assert "print(sys._base_executable)" in run
    assert run.count("if ($LASTEXITCODE -ne 0) { throw ") == 2
    assert "[string]::IsNullOrWhiteSpace($basePython)" in run
    assert "Test-Path -LiteralPath $basePython -PathType Leaf" in run
    assert "sys.version_info[:2] == (3, 11)" in run
    assert "sys.prefix == sys.base_prefix" in run
    assert "os.path.samefile(sys.executable, sys._base_executable)" in run
    assert run.rstrip().endswith('"SYLVIA_GM_TEST_PYTHON=$basePython" >> $env:GITHUB_ENV')


def test_rust_python_discovery_reuses_project_venv_without_a_managed_python_cache(tmp_path):
    import os
    import shlex
    import subprocess
    import sys
    import venv

    import yaml

    root = Path(__file__).resolve().parents[1]
    workflow = yaml.load(
        (root / ".github/workflows/supported-journeys.yml").read_text(), Loader=yaml.BaseLoader
    )
    setup = next(
        step
        for step in workflow["jobs"]["rust-tests"]["steps"]
        if step.get("name") == "Select base Python for Rust owner fixtures"
    )
    command = shlex.split(setup["run"].splitlines()[0].split(" = ", 1)[1])
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "hosted-toolcache-python"\nversion = "0.0.0"\n'
        'requires-python = ">=3.11"\n'
    )
    # Model a hosted-toolcache interpreter outside uv's managed store.
    venv.EnvBuilder(with_pip=False).create(tmp_path / ".venv")
    env = dict(
        os.environ,
        UV_PYTHON_INSTALL_DIR=str(tmp_path / "empty-managed-python"),
        UV_CACHE_DIR=str(tmp_path / "empty-uv-cache"),
        UV_OFFLINE="1",
    )
    subprocess.run(
        ["uv", "lock", "--offline", "--no-python-downloads"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    lock = (tmp_path / "uv.lock").read_bytes()
    result = subprocess.run(
        command, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()).samefile(sys._base_executable)
    assert (tmp_path / "uv.lock").read_bytes() == lock


@pytest.mark.parametrize("path", [
    "src/sylvia_gm/agent/_provider_session.py",
    "src/sylvia_gm/agent/_provider_turn_phases.py",
    "src/sylvia_gm/agent/_provider_turn_state.py",
    "src/sylvia_gm/agent/_provider_turn_decisions.py",
    "src/sylvia_gm/agent/_session_loop.py",
])
def test_gm_edits_select_full_behavioral_modules_without_campaign_replay(path):
    plan = choose([path])
    assert not plan["broad_fallback"] and not plan["native"] and not plan["ui"]
    assert plan["python"]["gm-turn"] == [
        "tests/test_alpha_f4_provider_session_adapter.py",
        "tests/test_alpha_f4_generic_sessions.py",
        "tests/test_provider_turn_state.py",
    ]
    assert "campaign-play" not in plan["python"]
    assert not any("test_action_sequence" in selector or "test_fc_campaign" in selector
                   for paths in plan["python"].values() for selector in paths)


def test_queue_edits_select_their_expensive_behavior_and_fast_provider_consumer():
    plan = choose(["src/sylvia_gm/application/action_sequences.py"])
    assert "gm-turn" in plan["python"]
    assert plan["python"]["action-sequences"] == [
        "tests/test_action_sequences.py", "tests/test_action_sequence_provider.py",
    ]
    assert "campaign-play" not in plan["python"]


@pytest.mark.parametrize("path", [
    "src-tauri/src/lib.rs",
    "scripts/build_windows_prerelease.py",
    "rulesets/srd-lite.sylvia-gm-package",
    "campaign-settings/severed-lands/setting.json",
    "campaign-plans/riichi-quick-test/plan.json",
    "src/sylvia_gm/ruleset/runtime/procedure_executor.py",
    "src/sylvia_gm/application/bundled_packages.py",
    "src/sylvia_gm/application/bundled_campaign_plans.py",
    "src/sylvia_gm/application/builder.py",
    "src/sylvia_gm/agent/skills/game-master/SKILL.md",
])
def test_session_dependencies_keep_behavioral_coverage_after_group_move(path):
    plan = choose([path])
    selectors = [item for values in plan["python"].values() for item in values]
    assert not plan["broad_fallback"]
    assert "interruption-and-recovery" in plan["python"]
    assert selectors.count("tests/test_alpha_f4_generic_sessions.py") == 1
    assert selectors.count("tests/test_alpha_f4_provider_session_adapter.py") == 1


def test_combined_session_dependency_diff_selects_each_module_once():
    plan = choose([
        "src-tauri/src/lib.rs", "rulesets/srd-lite.sylvia-gm-package",
        "src/sylvia_gm/ruleset/runtime/procedure_executor.py",
        "src/sylvia_gm/application/bundled_packages.py",
        "src/sylvia_gm/application/builder.py",
    ])
    selectors = [item for values in plan["python"].values() for item in values]
    assert len(selectors) == len(set(selectors))
    assert selectors.count("tests/test_alpha_f4_generic_sessions.py") == 1


@pytest.mark.parametrize("removed", ["tests/test_removed_behavior.py", "tests/removed_helper.py"])
def test_deleted_tests_select_existing_consumers_without_missing_required_paths(tmp_path, removed):
    import json
    from scripts.select_checks import load_selection

    root = Path(__file__).resolve().parents[1]
    assert not (root / removed).exists()
    plan = choose([removed], root=root)
    selectors = [item for values in plan["python"].values() for item in values]
    assert plan["broad_fallback"]
    assert removed not in selectors
    assert "tests/test_alpha_f4_generic_sessions.py" in selectors
    assert any("Removed test" in row["reason"] for row in plan["reasons"])
    manifest = tmp_path / "selection.json"
    manifest.write_text(json.dumps(plan))
    assert load_selection(manifest, revision="a" * 40, root=root) == plan


def test_guide_uses_one_plugin_loading_policy_for_standalone_and_runner_commands():
    root = Path(__file__).resolve().parents[1]
    guide = (root / "docs/operations/local-checks-and-release.md").read_text()
    block = guide.split("```powershell", 1)[1].split("```", 1)[0]
    standalone = block.index(".venv/Scripts/python.exe -m pytest")
    runner = block.index(".venv/Scripts/python.exe scripts/check_supported_journeys.py")
    assert block.index("$env:PYTHONDONTWRITEBYTECODE=1") < standalone < runner
    assert block.index("$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD=$null") < standalone
    assert "-p pytest_asyncio.plugin" not in block
    assert "$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD=1" not in block


@pytest.mark.parametrize("document", ["README.md", "docs/INDEX.md", "docs/operations/local-checks-and-release.md"])
def test_check_guide_edits_do_not_select_gm_or_campaign_runtime(document):
    plan = choose([document])
    assert set(plan["python"]) == {"copy-and-links"}
    assert not plan["ui"] and not plan["native"]
    assert plan["documents"] == [document]


def test_full_has_unique_whole_gm_modules_and_each_historical_campaign_module():
    from scripts.check_catalog import HISTORICAL_CHECKS
    plan = choose([], full=True)
    selectors = [selector for values in plan["python"].values() for selector in values]
    for paths in HISTORICAL_CHECKS["gm-turn-checkpoint"].values():
        for path in paths:
            assert selectors.count(path) == 1
    assert not any(selector.startswith("tests/test_alpha_f4_provider_session_adapter.py::")
                   for selector in selectors)
    assert selectors.count("tests/test_provider_turn_state.py") == 1
    assert "tests/test_provider_turn_state.py" in plan["python"]["gm-turn"]
    assert "tests/test_provider_turn_state.py" not in plan["python"]["provider-contracts"]


def test_product_exclusion_preserves_other_and_future_cases_and_historical_selection():
    from scripts.check_catalog import PRODUCT_CASE_EXCLUSIONS, qualification_cases
    historical = next(iter(PRODUCT_CASE_EXCLUSIONS))
    existing = "tests/test_alpha_f4_generic_sessions.py::test_existing_behavior"
    future = "tests/test_alpha_f4_generic_sessions.py::test_future_behavior[new-parameter]"
    required, excluded = qualification_cases([existing, historical, future])
    assert required == [existing, future]
    assert excluded == [{"nodeid": historical, "reason": PRODUCT_CASE_EXCLUSIONS[historical]}]
    assert qualification_cases([existing, historical, future], include_historical=True) == (
        [existing, historical, future], [],
    )


def test_historical_gm_check_keeps_original_modules(tmp_path):
    import json
    from scripts.check_catalog import HISTORICAL_CHECKS
    from scripts.select_checks import historical_check, load_selection

    plan = historical_check("gm-turn-checkpoint", source="a" * 40)
    assert plan["qualification_kind"] == "historical" and plan["mode"] == "affected"
    assert plan["python"] == HISTORICAL_CHECKS["gm-turn-checkpoint"]
    campaign = plan["python"]["campaign-play"]
    assert campaign == [
        "tests/test_campaign_rules_update.py", "tests/test_campaign_rules_update_bindings.py",
        "tests/test_campaign_rules_update_knowledge.py", "tests/test_action_sequences.py",
        "tests/test_action_sequence_provider.py", "tests/test_td_native_panel_repairs.py",
        "tests/test_fc_campaign_journey.py", "tests/test_fc_party_combat.py",
        "tests/test_fc_campaign_progress.py", "tests/test_migrated_plan_lifecycle.py",
        "tests/test_riichi_quick_test_plan.py", "tests/test_no_rules_interview.py",
    ]
    for paths in plan["python"].values():
        for relative in paths:
            path = tmp_path / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")
    manifest = tmp_path / "selection.json"
    manifest.write_text(json.dumps(plan))
    assert load_selection(manifest, revision="a" * 40, root=tmp_path) == plan
    plan["python"]["turn-provider-contracts"].pop()
    manifest.write_text(json.dumps(plan))
    with pytest.raises(ValueError, match="complete declared selection"):
        load_selection(manifest, revision="a" * 40, root=tmp_path)


def test_readme_resets_explicit_plugin_environment_before_runner_commands():
    import re

    root = Path(__file__).resolve().parents[1]
    readme = (root / "README.md").read_text()
    blocks = re.findall(r"```powershell\s+(.*?)```", readme, flags=re.DOTALL)
    standalone = next(block for block in blocks if "-m pytest" in block)
    runner = next(block for block in blocks if "scripts/check_supported_journeys.py" in block)
    assert standalone.index("$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD=1") < standalone.index("-p pytest_asyncio.plugin")
    assert runner.index("$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD=$null") < runner.index("scripts/select_checks.py")
    assert "$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD=1" not in runner
    assert "-p pytest_asyncio.plugin" not in runner



@pytest.mark.parametrize("path", [
    "src/sylvia_gm/agent/_provider_session.py",
    "src/sylvia_gm/agent/_provider_turn_phases.py",
    "src/sylvia_gm/agent/_provider_turn_state.py",
    "src/sylvia_gm/agent/_provider_turn_decisions.py",
    "src/sylvia_gm/agent/_session_loop.py",
    "src/sylvia_gm/agent/_stateless_mcp.py",
    "src/sylvia_gm/agent/_native_continuation.py",
    "src/sylvia_gm/agent/_provider_runtime.py",
    "src/sylvia_gm/provider/_base.py",
    "src/sylvia_gm/provider/_codex_rpc.py",
    "src/sylvia_gm/application/authority.py",
    "src/sylvia_gm/infrastructure/sqlite_authority.py",
    "src/sylvia_gm/infrastructure/sqlite_continuity.py",
    "src/sylvia_gm/domain/authority.py",
    "src/sylvia_gm/domain/errors.py",
    "src/sylvia_gm/application/package_runtime.py",
    "src/sylvia_gm/application/package_materialization.py",
    "src/sylvia_gm/application/bundled_packages.py",
    "src/sylvia_gm/application/bundled_campaign_plans.py",
    "src/sylvia_gm/runtime/_runtime.py",
    "src/sylvia_gm/runtime/_tool_executor.py",
    "src/sylvia_gm/runtime/_types.py",
    "rulesets/srd-lite/runtime/ruleset.yaml",
    "campaign-plans/riichi-quick-test/plan.json",
    "scripts/srd_lite_gm_feedback.py",
    "src-tauri/scripts/build_sylvia_owner.ps1",
])
def test_real_gm_request_contracts_follow_relevant_changes(path):
    plan = choose([path])
    module = "tests/test_gm_memory_casting_hotfix.py"
    assert plan["python"]["gm-request-contracts"] == [
        module, "tests/test_gm_schema_minimal_refusals.py",
    ]
    selectors = [item for values in plan["python"].values() for item in values]
    assert sum(item.split("::")[0] == module for item in selectors) == 1


@pytest.mark.parametrize("path", [
    "README.md", "docs/operations/local-checks-and-release.md",
    "src-tauri/frontend/app.js", "src-tauri/frontend/app.css",
    "scripts/select_checks.py",
])
def test_real_gm_request_contracts_are_not_selected_for_copy_or_harness_only_edits(path):
    plan = choose([path])
    assert "gm-request-contracts" not in plan["python"]


@pytest.mark.parametrize("module", [
    "tests/test_gm_memory_casting_hotfix.py", "tests/test_gm_schema_minimal_refusals.py",
])
def test_real_gm_request_contracts_have_one_full_owner_and_separate_quick_group(module):
    from scripts.check_supported_journeys import FULL_ONLY_JOURNEYS, PYTHON_JOURNEYS

    owners = [(group, item) for group, items in PYTHON_JOURNEYS.items()
              for item in items if item.split("::")[0] == module]
    assert owners == [("gm-request-contracts", module)]
    assert module not in PYTHON_JOURNEYS["gm-turn"]
    assert "gm-request-contracts" not in FULL_ONLY_JOURNEYS
    full = choose(["README.md"], full=True)
    assert full["python"]["gm-request-contracts"] == [
        "tests/test_gm_memory_casting_hotfix.py", "tests/test_gm_schema_minimal_refusals.py",
    ]
    assert full["python"]["gm-turn"] == PYTHON_JOURNEYS["gm-turn"]


def test_complete_full_selection_loads_against_the_actual_checkout(tmp_path):
    import json
    from scripts.select_checks import load_selection

    plan = choose(["README.md"], full=True)
    manifest = tmp_path / "full-selection.json"
    manifest.write_text(json.dumps(plan), encoding="utf-8")
    root = Path(__file__).resolve().parents[1]

    assert load_selection(manifest, revision="a" * 40, root=root) == plan


@pytest.mark.parametrize("module", [
    "tests/test_gm_memory_casting_hotfix.py", "tests/test_gm_schema_minimal_refusals.py",
])
def test_real_gm_request_contracts_are_unique_in_a_combined_affected_diff(module):
    plan = choose([
        "src/sylvia_gm/agent/_provider_session.py",
        "src/sylvia_gm/provider/_base.py",
        "src/sylvia_gm/application/authority.py",
        "src/sylvia_gm/application/package_runtime.py",
        module,
    ])
    owners = [(group, item) for group, items in plan["python"].items()
              for item in items if item.split("::")[0] == module]
    assert owners == [("gm-request-contracts", module)]
