from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from sylvia_gm.config._env import apply_env_overrides
from sylvia_gm.domain.package_library import content_root_for_record, manifest_from_record
from sylvia_gm.infrastructure.campaign_archive import FORMAT_ID, PRODUCT_ID

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
# This one module contains deliberate retired-name rejection fixtures.
_REJECTION_FIXTURE_PATHS = {"tests/test_sylvia_gm_identity.py"}
_RETIRED_IDENTITY = "retired-checkout-fixture"


def _assert_canonical_readme_checkout_commands(content: bytes) -> None:
    markdown = content.decode("utf-8")
    for destination in re.findall(r"(?m)^\s*git\s+clone\s+(\S+)", markdown):
        assert destination.strip("\"'") in {
            "https://github.com/pneuma-io/sylvia-gm",
            "https://github.com/pneuma-io/sylvia-gm.git",
        }
    for directory in re.findall(r"(?m)^\s*cd\s+(\S+)", markdown):
        assert directory.strip("\"'") in {"sylvia-gm", "src-tauri", ".."}


def test_sylvia_gm_is_the_canonical_project_identity() -> None:
    pyproject = tomllib.loads((REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["name"] == "sylvia-gm"
    assert pyproject["project"]["scripts"] == {
        "sylvia-gm": "sylvia_gm.main:main",
        "sylvia-gm-owner": "sylvia_gm.interfaces.owner_process:main",
    }

    assert (REPOSITORY_ROOT / "src" / "sylvia_gm").is_dir()
    assert not (REPOSITORY_ROOT / "src" / f"{_RETIRED_IDENTITY}_dm").exists()

    tauri = json.loads(
        (REPOSITORY_ROOT / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8")
    )
    assert tauri["productName"] == "Sylvia GM"
    assert tauri["identifier"] == "com.pneuma-io.sylvia-gm"
    assert tauri["bundle"]["externalBin"] == ["binaries/sylvia-gm-owner"]

    assert (REPOSITORY_ROOT / "src-tauri" / "scripts" / "sylvia_gm_owner_entry.py").is_file()
    assert (REPOSITORY_ROOT / "src-tauri" / "frontend" / "assets" / "sylvia-portrait.png").is_file()
    assert (REPOSITORY_ROOT / "src-tauri" / "icons" / "icon.ico").is_file()


def test_sylvia_gm_structural_identifiers_are_exact_and_old_package_ids_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert FORMAT_ID == "sylvia-gm-campaign-export"
    assert PRODUCT_ID == "sylvia-gm"

    package_record = json.loads(
        (REPOSITORY_ROOT / "rulesets" / "srd-lite" / "package-record.json").read_text(
            encoding="utf-8"
        )
    )
    fixture_record = json.loads(
        (
            REPOSITORY_ROOT
            / "tests"
            / "fixtures"
            / "rulesets"
            / "signal-and-static"
            / "package-record.json"
        ).read_text(encoding="utf-8")
    )
    compatibility = {"host_contract": 1, "runtime_format": 1, "requires": []}
    assert package_record["compatibility"] == {
        **compatibility,
        "requires": [
            "campaign_starting_events.v1",
            "character_rule_results.v1",
            "entity_creation.v1",
            "participant_mutation.v1",
            "required_starting_state.v1",
            "runtime_immutable_collections.v1",
            "runtime_require.v1",
            "starting_state_validation.v1",
            "tactical_grid.v1",
        ],
    }
    assert fixture_record["compatibility"] == compatibility

    runtime = (REPOSITORY_ROOT / "rulesets" / "srd-lite" / "runtime" / "ruleset.yaml").read_text(
        encoding="utf-8"
    )
    assert runtime.startswith("sylvia_gm_runtime:\n")
    assert "x-sylvia-gm-reference" in json.dumps(package_record, sort_keys=True)

    minimal_record: dict[str, object] = {
        "id": "example.identity",
        "version": "0.1.0",
        "role": "primary",
        "metadata": {"label": "Identity probe"},
        "provenance": {"publisher": "Identity probe", "source_sha256": "0" * 64},
        "compatibility": {"host_contract": 1, "runtime_format": 1, "requires": []},
        "minimum_model_capability_tier": "sylvia.capability.t00",
        "dependencies": [],
        "capabilities": [],
        "tools": [],
        "prompt_fragments": [],
        "ui_surfaces": [],
        "files": [],
    }
    minimal_record["content_sha256"] = content_root_for_record(minimal_record)
    assert manifest_from_record(minimal_record, format_version=1).package_id == "example.identity"

    retired_record = deepcopy(minimal_record)
    retired_record["compatibility"] = {_RETIRED_IDENTITY: "alpha"}
    retired_record["content_sha256"] = content_root_for_record(retired_record)
    with pytest.raises(ValueError, match="Package compatibility"):
        manifest_from_record(retired_record, format_version=1)

    monkeypatch.setenv("SYLVIA_GM_APP__NAME", "Canonical")
    monkeypatch.setenv(f"{_RETIRED_IDENTITY.upper()}_APP__NAME", "Retired")
    assert apply_env_overrides({"app": {"name": "Default"}})["app"]["name"] == "Canonical"
    monkeypatch.delenv("SYLVIA_GM_APP__NAME")
    assert apply_env_overrides({"app": {"name": "Default"}})["app"]["name"] == "Default"


def test_current_product_surfaces_reject_retired_identity() -> None:
    tracked = (
        subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=REPOSITORY_ROOT,
            check=True,
            capture_output=True,
        )
        .stdout.decode("utf-8")
        .split("\0")
    )
    untracked = (
        subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"],
            cwd=REPOSITORY_ROOT,
            check=True,
            capture_output=True,
        )
        .stdout.decode("utf-8")
        .split("\0")
    )

    violations: list[str] = []
    for relative in sorted(set(tracked + untracked)):
        if not relative or relative in _REJECTION_FIXTURE_PATHS:
            continue
        normalized = relative.replace("\\", "/")
        if _RETIRED_IDENTITY in normalized.casefold():
            violations.append(f"retired path: {normalized}")
            continue
        path = REPOSITORY_ROOT / relative
        content = path.read_bytes().lower() if path.is_file() else b""
        if normalized == "README.md":
            _assert_canonical_readme_checkout_commands(content)
        if _RETIRED_IDENTITY.encode("ascii") in content:
            violations.append(f"retired content: {normalized}")

    assert violations == []


def test_canonical_frontend_presents_sylvia_without_pneuma_cobrand() -> None:
    html = (REPOSITORY_ROOT / "src-tauri" / "frontend" / "app.html").read_text(encoding="utf-8")
    script = (REPOSITORY_ROOT / "src-tauri" / "frontend" / "app.js").read_text(encoding="utf-8")
    assert "Sylvia GM" in html
    assert 'src="assets/sylvia-portrait.png" alt=""' in html
    assert '<span class="portrait-label">Sylvia</span>' in html
    assert '<span class="portrait-sub">Game Master</span>' in html
    assert "Session Guide" not in html
    assert "SylviaGMFrontend" in script
    assert "__sylviaGMBridge" in script
    assert "Pneuma IO" not in html
    assert "Pneuma IO" not in script


@pytest.mark.parametrize(
    "retired_command",
    [
        f"git clone https://github.com/pneuma-io/{_RETIRED_IDENTITY}-dm.git",
        f"cd {_RETIRED_IDENTITY}-dm",
    ],
)
def test_readme_retired_checkout_commands_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, retired_command: str
) -> None:
    (tmp_path / "README.md").write_text("# Sylvia GM\n" + retired_command + "\n")
    monkeypatch.setattr(sys.modules[__name__], "REPOSITORY_ROOT", tmp_path)
    monkeypatch.setattr(
        subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(stdout=b"README.md\0")
    )
    with pytest.raises(AssertionError):
        test_current_product_surfaces_reject_retired_identity()


def test_informational_cli_cannot_launch_a_competing_application() -> None:
    help_result = subprocess.run(
        [sys.executable, "-m", "sylvia_gm", "--help"],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert help_result.returncode == 0
    assert "installed Windows desktop application" in help_result.stdout
    assert "{serve,desktop}" not in help_result.stdout
    assert "Start the HTTP server" not in help_result.stdout

    version_result = subprocess.run(
        [sys.executable, "-m", "sylvia_gm", "--version"],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert version_result.returncode == 0
    assert version_result.stdout.strip().startswith("sylvia-gm 0.3.0")

    for retired_command in ("serve", "desktop"):
        retired_result = subprocess.run(
            [sys.executable, "-m", "sylvia_gm", retired_command],
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        assert retired_result.returncode == 2
        assert "unrecognized arguments" in retired_result.stderr
