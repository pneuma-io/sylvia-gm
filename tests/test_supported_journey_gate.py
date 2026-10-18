"""Fail-closed qualification of the actual local supported-journey runner."""

import importlib.util
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def gate():
    path = Path(__file__).resolve().parents[1] / "scripts/check_supported_journeys.py"
    spec = importlib.util.spec_from_file_location("qa_supported_gate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "failure",
    [
        "missing",
        "empty",
        "omitted",
        "skipped",
        "failure",
        "error",
        "malformed",
        "nonzero",
        "timeout",
    ],
)
def test_runner_rejects_incomplete_or_failed_required_checks(gate, tmp_path, monkeypatch, failure):
    from types import SimpleNamespace

    calls = []

    def execute(command, **kwargs):
        calls.append(command)
        assert "PYTEST_ADDOPTS" not in kwargs["env"]
        assert kwargs["timeout"] is None
        if "--collect-only" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                "tests/test_required.py::test_one\ntests/test_required.py::test_two\n",
                "",
            )
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 900)
        path = tmp_path / "campaign-play.xml"
        if failure != "missing":
            if failure == "malformed":
                path.write_text("<bad")
            else:
                cases = (
                    []
                    if failure == "empty"
                    else ['<testcase classname="tests.test_required" name="test_one"/>']
                )
                if failure not in ("empty", "omitted", "skipped", "failure", "error"):
                    cases += ['<testcase classname="tests.test_required" name="test_two"/>']
                elif failure in ("skipped", "failure", "error"):
                    cases += [
                        f'<testcase classname="tests.test_required" name="test_two">'
                        f"<{failure}/></testcase>"
                    ]
                path.write_text(
                    "<testsuites><testsuite>" + "".join(cases) + "</testsuite></testsuites>"
                )
        return subprocess.CompletedProcess(command, 1 if failure == "nonzero" else 0, "", "")

    monkeypatch.setenv("PYTEST_ADDOPTS", "-k test_one")
    monkeypatch.setattr(
        gate,
        "subprocess",
        SimpleNamespace(
            run=execute, TimeoutExpired=subprocess.TimeoutExpired, STDOUT=subprocess.STDOUT
        ),
    )
    assert gate._run("campaign-play", tmp_path)[0] is False
    assert len(calls) == 2


def test_revision_guard_and_hash_include_new_source_bytes(gate, tmp_path, monkeypatch):
    monkeypatch.delenv("SYLVIA_EXPECTED_REVISION", raising=False)
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    source = tmp_path / "src/module.py"
    source.parent.mkdir()
    source.write_text("first")
    first = gate.source_identity()
    source.write_text("second")
    assert gate.source_identity()["sha256"] != first["sha256"]
    monkeypatch.setenv("SYLVIA_EXPECTED_REVISION", "expected-head")
    with pytest.raises(ValueError, match="revision differs"):
        gate.source_identity()


@pytest.mark.parametrize(
    "mode",
    [
        "complete",
        "missing",
        "duplicate",
        "wrong",
        "skipped",
        "failure",
        "nonzero",
        "malformed",
        "timeout",
    ],
)
def test_parallel_merge_requires_every_original_case_exactly_once(
    gate, tmp_path, monkeypatch, mode
):
    import json
    import xml.etree.ElementTree as ET
    from types import SimpleNamespace

    def execute(command, **kwargs):
        assert "PYTEST_ADDOPTS" not in kwargs["env"]
        if "--collect-only" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                "tests/test_required.py::test_one\ntests/test_required.py::test_two\n",
                "",
            )
        args = json.loads(Path(command[-1]).read_text(encoding="utf-8"))
        xml = Path(
            next(value.split("=", 1)[1] for value in args if value.startswith("--junitxml="))
        )
        selected = [value for value in args if value.startswith("tests/")]
        assert len(selected) == 1
        name = selected[0].split("::")[-1]
        if mode == "timeout" and name == "test_two":
            raise subprocess.TimeoutExpired(command, 900)
        if mode == "missing" and name == "test_two":
            return subprocess.CompletedProcess(command, 0, "", "")
        if mode == "malformed" and name == "test_two":
            xml.write_text("<broken", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, "", "")
        root = ET.Element("testsuites")
        suite = ET.SubElement(root, "testsuite")
        case = ET.SubElement(
            suite,
            "testcase",
            classname="tests.test_required",
            name="test_one" if mode == "wrong" else name,
        )
        if mode == "duplicate":
            ET.SubElement(suite, "testcase", classname="tests.test_required", name=name)
        if mode in {"skipped", "failure"} and name == "test_two":
            ET.SubElement(case, mode)
        ET.ElementTree(root).write(xml, encoding="utf-8")
        return subprocess.CompletedProcess(command, 1 if mode == "nonzero" else 0, "", "")

    monkeypatch.setenv("PYTEST_ADDOPTS", "--maxfail=1")
    monkeypatch.setattr(
        gate,
        "subprocess",
        SimpleNamespace(
            run=execute, TimeoutExpired=subprocess.TimeoutExpired, STDOUT=subprocess.STDOUT
        ),
    )
    passed, _ = gate._run("campaign-play", tmp_path, case_workers=2)
    assert passed is (mode == "complete")


def test_early_group_setup_failure_remains_failed_required_evidence(gate, tmp_path, monkeypatch):
    def unavailable(*args, **kwargs):
        raise ValueError("owned test data is not fresh")

    monkeypatch.setattr(gate, "_run", unavailable)
    passed, _ = gate._run_guarded("lite-admitted-qa", tmp_path)
    assert passed is False
    assert "owned test data is not fresh" in (tmp_path / "lite-admitted-qa.log").read_text()


def test_relative_cli_output_is_absolute_before_worker_cwd_changes(gate, tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    observed = []

    def balanced(root, destination, journeys, selected, count, started, run_ui, *, deadline=None, scheduler="module"):
        assert destination == (tmp_path / "relative-output").resolve()
        assert destination.is_absolute()
        assert deadline is None
        observed.append(destination)
        return dict.fromkeys(selected, (True, 0.0))

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(gate, "PYTHON_JOURNEYS", {"required": ["tests/test_required.py"]})
    monkeypatch.setattr(
        gate, "source_identity", lambda: {"sha256": "unchanged", "head": None, "files": {}}
    )
    monkeypatch.setitem(sys.modules, "journey_pool", SimpleNamespace(run_balanced_gate=balanced))
    monkeypatch.setattr(sys, "argv", ["gate", "--output-dir", "relative-output"])
    assert gate.main() == 0
    assert len(observed) == 1


@pytest.mark.parametrize("ci", [False, True])
def test_development_runs_can_complete_after_ci_allowance(gate, tmp_path, monkeypatch, ci):
    import json
    import sys
    from types import SimpleNamespace

    def balanced(root, destination, journeys, selected, count, started, run_ui, *, deadline=None, scheduler="module"):
        assert deadline == (1800 if ci else None)
        return dict.fromkeys(selected, (True, 2001.0))

    clock = iter([0.0, 2001.0])
    monkeypatch.setattr(gate, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    monkeypatch.setattr(gate, "PYTHON_JOURNEYS", {"required": ["tests/test_required.py"]})
    monkeypatch.setattr(
        gate, "source_identity", lambda: {"sha256": "same", "head": None, "files": {}}
    )
    monkeypatch.setitem(sys.modules, "journey_pool", SimpleNamespace(run_balanced_gate=balanced))
    args = ["gate", "--output-dir", str(tmp_path / "output")]
    if ci:
        args.append("--ci")
    monkeypatch.setattr(sys, "argv", args)
    assert gate.main() == (1 if ci else 0)
    summary = json.loads((tmp_path / "output/summary.json").read_text())
    assert summary["qualification_mode"] == ("ci" if ci else "development")
    assert summary["time_budget_seconds"] == (1800 if ci else None)
    assert summary["passed"] is (not ci)


def test_required_notice_bytes_change_the_source_identity(gate, tmp_path, monkeypatch):
    monkeypatch.delenv("SYLVIA_EXPECTED_REVISION", raising=False)
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    notice = tmp_path / "dependency-notices.txt"
    notice.write_text("Synthetic required notice one\n", encoding="utf-8")
    first = gate.source_identity()
    assert "dependency-notices.txt" in first["files"]
    notice.write_text("Synthetic required notice two\n", encoding="utf-8")
    assert gate.source_identity()["sha256"] != first["sha256"]


def test_generated_native_script_bytecode_is_not_a_changed_source_input(gate, tmp_path, monkeypatch):
    monkeypatch.delenv("SYLVIA_EXPECTED_REVISION", raising=False)
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    script = tmp_path / "src-tauri/scripts/build.py"
    script.parent.mkdir(parents=True)
    script.write_text("real source")
    first = gate.source_identity()
    generated = script.parent / "__pycache__/build.cpython-311.pyc"
    generated.parent.mkdir()
    generated.write_bytes(b"generated during a test")
    assert gate.source_identity() == first
    script.write_text("changed real source")
    assert gate.source_identity()["sha256"] != first["sha256"]


def test_normal_gate_selects_existing_protection_and_release_suites(gate):
    required = {
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
    }
    assert required <= set(gate.PYTHON_JOURNEYS["release-integrity"])
    assert set(gate.PYTHON_JOURNEYS["generic-procedures"]) == {
        "tests/test_runtime_procedure_failures.py",
        "tests/test_ruleset_modifier_evaluation.py",
        "tests/test_runtime_nested_rejections.py",
    }


def test_complete_gate_cannot_report_green_when_runner_omits_a_group(gate, tmp_path, monkeypatch):
    import json
    import sys
    from types import SimpleNamespace

    monkeypatch.setattr(gate, "PYTHON_JOURNEYS", {"required": ["tests/test_required.py"]})
    monkeypatch.setattr(
        gate, "source_identity", lambda: {"sha256": "same", "head": None, "files": {}}
    )
    monkeypatch.setitem(
        sys.modules, "journey_pool", SimpleNamespace(run_balanced_gate=lambda *a, **k: {})
    )
    monkeypatch.setattr(sys, "argv", ["gate", "--output-dir", str(tmp_path / "results")])
    assert gate.main() == 1
    result = json.loads((tmp_path / "results/summary.json").read_text())
    assert not result["passed"]
    assert result["missing_groups"] == ["required", "ui-controls"]


def test_workflow_includes_native_unit_tests_and_explicit_future_budget():
    import yaml

    root = Path(__file__).resolve().parents[1]
    text = (root / ".github/workflows/supported-journeys.yml").read_text()
    workflow = yaml.safe_load(text)
    commands = [step.get("run", "") for step in workflow["jobs"]["rust-tests"]["steps"]]
    assert any("cargo test --locked --all-targets" in command for command in commands)
    assert any("build_owner_sidecar.py" in command for command in commands)
    assert "post_launch_ci_budget" in text and "default: false" in text
    assert "workflow_dispatch' -and" in text
    assert "timeout-minutes" not in text
