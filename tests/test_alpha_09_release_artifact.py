"""Alpha-09 deterministic Windows prerelease identity and layout."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import sysconfig
import venv
from pathlib import Path

import pytest
from scripts import build_windows_prerelease as windows_builder
from scripts import qualify_windows_msi as msi_qualification
from scripts import qualify_windows_nsis as nsis_qualification
from scripts.build_windows_prerelease import (
    LOCK_CHECK_COMMANDS,
    TAURI_BUILD_COMMAND,
    TAURI_WINDOWS_VERSION_OVERRIDE,
    discover_installer_artifacts,
)

from sylvia_gm.release_artifact import (
    DEPENDENCY_NOTICES_PATH,
    DEPENDENCY_NOTICES_SOURCE,
    PRERELEASE_VERSION,
    WINDOWS_INSTALLER_VERSION,
    ReleaseArtifact,
    ReleaseArtifactError,
    assemble_qualified_release_layout,
    assemble_release_layout,
    read_dependency_notices,
    validate_version_sources,
    verify_release_layout,
)

ROOT = Path(__file__).parents[1]
SYNTHETIC_NOTICES = (
    b"SYNTHETIC TEST FIXTURE ONLY - no shipping licence text.\r\nExample \xc2\xa9.\r\n"
)


def test_documented_builder_help_works_with_only_src_installed(tmp_path: Path) -> None:
    environment_root = tmp_path / "source-install-environment"
    venv.EnvBuilder(with_pip=False).create(environment_root)
    if os.name == "nt":
        executable = environment_root / "Scripts/python.exe"
        site_packages = environment_root / "Lib/site-packages"
    else:
        executable = environment_root / "bin/python"
        site_packages = (
            environment_root
            / f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
        )
    # Match the src-only editable install and reuse installed dependencies,
    # without repo-root injection.
    (site_packages / "selected-source-and-dependencies.pth").write_text(
        str(ROOT / "src") + "\n" + sysconfig.get_path("purelib") + "\n", encoding="utf-8"
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() not in {"PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"}
    }
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [str(executable), "-B", "scripts/build_windows_prerelease.py", "--help"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "--dependency-notices-sha256" in result.stdout
    assert "--forbidden-name-file" in result.stdout


@pytest.fixture
def synthetic_notices(tmp_path: Path) -> dict[str, object]:
    path = tmp_path / "synthetic-dependency-notices.txt"
    path.write_bytes(SYNTHETIC_NOTICES)
    return {
        "dependency_notices_path": path,
        "dependency_notices_sha256": hashlib.sha256(SYNTHETIC_NOTICES).hexdigest(),
    }


def _synthetic_release_notice_record(release: Path) -> dict[str, object]:
    path = release / DEPENDENCY_NOTICES_PATH
    path.parent.mkdir(parents=True)
    path.write_bytes(SYNTHETIC_NOTICES)
    return {
        "path": DEPENDENCY_NOTICES_PATH,
        "sha256": hashlib.sha256(SYNTHETIC_NOTICES).hexdigest(),
        "size_bytes": len(SYNTHETIC_NOTICES),
    }


def test_alpha_09_prerelease_version_is_exact_host_displayed_and_unsigned() -> None:
    from sylvia_gm.config.loader import load_config

    assert PRERELEASE_VERSION == "0.3.0-alpha.1"
    assert validate_version_sources(ROOT) == PRERELEASE_VERSION
    default_config = load_config(load_dotenv_file=False)
    assert default_config.app.version == PRERELEASE_VERSION
    assert default_config.ruleset.default == ""

    html = (ROOT / "src-tauri/frontend/app.html").read_text(encoding="utf-8")
    js = (ROOT / "src-tauri/frontend/app.js").read_text(encoding="utf-8")
    rust = (ROOT / "src-tauri/src/lib.rs").read_text(encoding="utf-8")
    settings = html[html.index("<!-- Dedicated settings surface") :]

    assert 'data-settings-tab="about"' in settings
    assert 'data-settings-panel="about"' in settings
    assert 'id="application-version"' in settings
    assert "unsigned" in settings.casefold()
    assert "invoke('application_info')" in js
    assert "info.version" in js
    assert 'env!("CARGO_PKG_VERSION")' in rust
    assert "fn application_info(" in rust


def test_alpha_09_release_layout_is_canonical_hash_exact_and_self_verifying(
    tmp_path: Path,
    synthetic_notices: dict[str, object],
) -> None:
    nsis = tmp_path / f"Sylvia.GM_{PRERELEASE_VERSION}_x64-setup.exe"
    nsis.write_bytes(b"synthetic-nsis-installer")
    inputs = (ReleaseArtifact(source=nsis, kind="nsis-installer"),)
    provenance = {
        "source_commit": "a" * 40,
        "source_tree": "b" * 40,
        "source_timestamp_utc": "2026-08-11T00:00:00Z",
    }
    toolchain = {
        "cargo": "cargo 1.97.0",
        "mcp": "2.0.0",
        "pyinstaller": "6.22.0",
        "python": "Python 3.11.15",
        "rustc": "rustc 1.97.0",
        "tauri_cli": "tauri-cli 2.8.0",
        "uv": "uv 0.11.6",
    }
    qualification = {
        "windows_10": "pending",
        "windows_11": "pending",
    }

    first = tmp_path / "release-one"
    second = tmp_path / "release-two"
    first_manifest = assemble_release_layout(
        **synthetic_notices,
        output=first,
        artifacts=inputs,
        provenance=provenance,
        toolchain=toolchain,
        qualification=qualification,
        license_path=ROOT / "LICENSE",
    )
    second_manifest = assemble_release_layout(
        **synthetic_notices,
        output=second,
        artifacts=inputs,
        provenance=provenance,
        toolchain=toolchain,
        qualification=qualification,
        license_path=ROOT / "LICENSE",
    )

    assert first_manifest == second_manifest
    assert (first / "release-manifest.json").read_bytes() == (
        second / "release-manifest.json"
    ).read_bytes()
    parsed = json.loads((first / "release-manifest.json").read_text(encoding="utf-8"))
    assert parsed["product"] == {
        "architecture": "x86_64",
        "channel": "alpha",
        "name": "Sylvia GM",
        "platform": "windows",
        "version": PRERELEASE_VERSION,
        "windows_installer_version": WINDOWS_INSTALLER_VERSION,
    }
    assert parsed["signing"]["status"] == "unsigned"
    assert parsed["qualification"] == qualification
    assert [item["name"] for item in parsed["artifacts"]] == [nsis.name]
    assert verify_release_layout(first) == parsed
    checksums = (first / "SHA256SUMS.txt").read_text(encoding="utf-8")
    assert "release-manifest.json" in checksums
    assert nsis.name in checksums
    assert DEPENDENCY_NOTICES_PATH in checksums
    assert (first / DEPENDENCY_NOTICES_PATH).read_bytes() == SYNTHETIC_NOTICES
    assert parsed["dependency_notices"] == {
        "path": DEPENDENCY_NOTICES_PATH,
        "sha256": hashlib.sha256(SYNTHETIC_NOTICES).hexdigest(),
        "size_bytes": len(SYNTHETIC_NOTICES),
    }

    qualified = tmp_path / "release-qualified"
    qualified_manifest = assemble_qualified_release_layout(
        source=first,
        output=qualified,
        qualification={"windows_10": "not_available", "windows_11": "passed"},
    )
    assert qualified_manifest["qualification"] == {
        "windows_10": "not_available",
        "windows_11": "passed",
    }
    assert (qualified / nsis.name).read_bytes() == (first / nsis.name).read_bytes()
    assert (qualified / DEPENDENCY_NOTICES_PATH).read_bytes() == SYNTHETIC_NOTICES
    assert (qualified / "LICENSE").read_bytes() == (ROOT / "LICENSE").read_bytes()
    assert qualified_manifest["provenance"] == first_manifest["provenance"]
    assert qualified_manifest["artifacts"] == first_manifest["artifacts"]
    assert verify_release_layout(qualified) == qualified_manifest

    (first / nsis.name).write_bytes(b"x" * len(b"synthetic-nsis-installer"))
    with pytest.raises(ReleaseArtifactError, match="hash"):
        verify_release_layout(first)

    parsed["toolchain"]["untrusted_extra"] = "not part of the release schema"
    (second / "release-manifest.json").write_bytes(
        (
            json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
    )
    with pytest.raises(ReleaseArtifactError, match="toolchain keys"):
        verify_release_layout(second)


def test_alpha_09_release_layout_fails_closed_on_unsafe_or_incoherent_input(
    tmp_path: Path,
    synthetic_notices: dict[str, object],
) -> None:
    installer = tmp_path / "setup.exe"
    msi = tmp_path / "setup.msi"
    installer.write_bytes(b"installer")
    msi.write_bytes(b"installer")
    base = {
        **synthetic_notices,
        "output": tmp_path / "release",
        "artifacts": (ReleaseArtifact(installer, "nsis-installer"),),
        "provenance": {
            "source_commit": "a" * 40,
            "source_tree": "b" * 40,
            "source_timestamp_utc": "2026-08-11T00:00:00Z",
        },
        "toolchain": {
            "cargo": "cargo 1.97.0",
            "mcp": "2.0.0",
            "pyinstaller": "6.22.0",
            "python": "Python 3.11.15",
            "rustc": "rustc 1.97.0",
            "tauri_cli": "tauri-cli 2.8.0",
            "uv": "uv 0.11.6",
        },
        "qualification": {"windows_10": "pending", "windows_11": "pending"},
        "license_path": ROOT / "LICENSE",
    }

    with pytest.raises(ReleaseArtifactError, match="prerelease version"):
        assemble_release_layout(**base, version="0.3.0")
    with pytest.raises(ReleaseArtifactError, match="artifact kind"):
        assemble_release_layout(
            **{
                **base,
                "artifacts": (ReleaseArtifact(installer, "portable-mystery"),),
            }
        )
    with pytest.raises(ReleaseArtifactError, match="exactly one NSIS"):
        assemble_release_layout(
            **{
                **base,
                "artifacts": (ReleaseArtifact(msi, "msi-installer"),),
            }
        )
    with pytest.raises(ReleaseArtifactError, match="already exists"):
        (tmp_path / "release").mkdir()
        assemble_release_layout(**base)


def test_alpha_09_windows_builder_is_locked_unsigned_and_selects_exact_bundles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert TAURI_BUILD_COMMAND == (
        "cargo",
        "tauri",
        "build",
        "--ci",
        "--no-sign",
        "--bundles",
        "nsis",
        "--config",
        '{"version":"0.3.0-1"}',
    )
    assert WINDOWS_INSTALLER_VERSION == "0.3.0-1"
    assert TAURI_WINDOWS_VERSION_OVERRIDE == '{"version":"0.3.0-1"}'
    assert ("uv", "lock", "--check") in LOCK_CHECK_COMMANDS
    assert any("--locked" in command for command in LOCK_CHECK_COMMANDS)

    bundle = tmp_path / "src-tauri" / "target" / "release" / "bundle"
    nsis = bundle / "nsis" / "Sylvia_GM-setup.exe"
    nsis.parent.mkdir(parents=True)
    nsis.write_bytes(b"nsis")

    artifacts = discover_installer_artifacts(tmp_path)
    assert [(item.kind, item.source.name) for item in artifacts] == [
        ("nsis-installer", nsis.name),
    ]

    (nsis.parent / "unexpected.exe").write_bytes(b"duplicate")
    with pytest.raises(ReleaseArtifactError, match="exactly one NSIS"):
        discover_installer_artifacts(tmp_path)

    monkeypatch.setattr(
        windows_builder,
        "_run",
        lambda *_args, **_kwargs: " M src/sylvia_gm/private_change.py",
    )
    with pytest.raises(ReleaseArtifactError, match="clean committed worktree"):
        windows_builder._require_clean_tree(tmp_path)


@pytest.mark.parametrize(
    "location_form",
    ["missing-open", "missing-close", "extra-quotes", "argument-suffix", "foreign-target"],
)
def test_alpha_09_nsis_quoted_location_keeps_exact_ownership(
    tmp_path: Path,
    location_form: str,
) -> None:
    target = tmp_path / "intended install"
    locations = {
        "missing-open": f'{target}"',
        "missing-close": f'"{target}',
        "extra-quotes": f'""{target}""',
        "argument-suffix": f'"{target}" --unexpected',
        "foreign-target": f'"{tmp_path / "foreign install"}"',
    }
    registration = nsis_qualification.UninstallRegistration(
        hive="HKCU",
        key=r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Sylvia GM",
        display_name="Sylvia GM",
        display_version=WINDOWS_INSTALLER_VERSION,
        install_location=locations[location_form],
        uninstall_string=f'"{target / "uninstall.exe"}"',
    )
    assert not nsis_qualification._registration_matches(registration, target)


def test_alpha_09_nsis_qualification_is_isolated_manifest_bound_and_removes_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    synthetic_notices: dict[str, object],
) -> None:
    installer = tmp_path / "synthetic-setup.exe"
    installer.write_bytes(b"synthetic-nsis")
    release = tmp_path / "release"
    assemble_release_layout(
        **synthetic_notices,
        output=release,
        artifacts=(ReleaseArtifact(installer, "nsis-installer"),),
        provenance={
            "source_commit": "a" * 40,
            "source_tree": "b" * 40,
            "source_timestamp_utc": "2026-08-11T00:00:00Z",
        },
        toolchain={
            "cargo": "cargo 1.97.0",
            "mcp": "2.0.0",
            "pyinstaller": "6.22.0",
            "python": "Python 3.11.15",
            "rustc": "rustc 1.97.0",
            "tauri_cli": "tauri-cli 2.8.0",
            "uv": "uv 0.11.6",
        },
        qualification={"windows_10": "pending", "windows_11": "pending"},
        license_path=ROOT / "LICENSE",
    )
    install_directory = tmp_path / "isolated install"
    registrations: list[nsis_qualification.UninstallRegistration] = []
    commands: list[tuple[str, ...]] = []

    def registrations_now() -> tuple[nsis_qualification.UninstallRegistration, ...]:
        return tuple(registrations)

    def run_checked(command: tuple[str, ...], *, timeout_seconds: float) -> None:
        assert timeout_seconds == 120.0
        commands.append(command)
        if command[0] == str(release / installer.name):
            assert command[1] == "/S"
            assert command[2] == f"/D={install_directory}"
            install_directory.mkdir()
            for name in ("sylvia-gm.exe", "sylvia-gm-owner.exe", "uninstall.exe"):
                (install_directory / name).write_bytes(name.encode("ascii"))
            notice = install_directory / DEPENDENCY_NOTICES_PATH
            notice.parent.mkdir()
            notice.write_bytes(SYNTHETIC_NOTICES)
            registrations.append(
                nsis_qualification.UninstallRegistration(
                    hive="HKCU",
                    key=r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Sylvia GM",
                    display_name="Sylvia GM",
                    display_version=WINDOWS_INSTALLER_VERSION,
                    # Match the actual generated NSIS registry writer, including quotes.
                    install_location=f'"{install_directory}"',
                    uninstall_string=f'"{install_directory / "uninstall.exe"}"',
                )
            )
        else:
            assert command == (str(install_directory / "uninstall.exe"), "/S")
            shutil.rmtree(install_directory)
            registrations.clear()

    monkeypatch.setattr(nsis_qualification, "_uninstall_registrations", registrations_now)
    monkeypatch.setattr(nsis_qualification, "_run_checked", run_checked)
    monkeypatch.setattr(
        nsis_qualification,
        "_windows_host",
        lambda: {
            "architecture": "AMD64",
            "build": 26200,
            "major": 10,
            "minor": 0,
            "platform_version": "10.0.26200",
        },
    )
    monkeypatch.setattr(
        nsis_qualification,
        "_qualify_installed_owner_backup_recovery",
        lambda *, owner_executable, workspace, timeout_seconds: {
            "backup_reopened": owner_executable == install_directory / "sylvia-gm-owner.exe",
            "captured_revision": 4,
            "portable_recovery": workspace == install_directory.parent,
            "rift_played": True,
        },
    )

    receipt = nsis_qualification.qualify_nsis_install_uninstall(
        release_directory=release,
        install_directory=install_directory,
        timeout_seconds=120.0,
    )

    assert receipt == {
        "application_executable": "sylvia-gm.exe",
        "dependency_notices": {
            "matches_release": True,
            "path": DEPENDENCY_NOTICES_PATH,
            "readable_after_owner_exit": True,
        },
        "host": {
            "architecture": "AMD64",
            "build": 26200,
            "major": 10,
            "minor": 0,
            "platform_version": "10.0.26200",
        },
        "install_directory_removed": True,
        "installer_kind": "nsis-installer",
        "installer_name": installer.name,
        "installer_sha256": hashlib.sha256(b"synthetic-nsis").hexdigest(),
        "installed_owner": {
            "backup_reopened": True,
            "captured_revision": 4,
            "portable_recovery": True,
            "rift_played": True,
        },
        "owner_executable": "sylvia-gm-owner.exe",
        "product": "Sylvia GM",
        "registration_hive": "HKCU",
        "registration_removed": True,
        "source_commit": "a" * 40,
        "source_tree": "b" * 40,
        "uninstaller_executable": "uninstall.exe",
        "version": PRERELEASE_VERSION,
        "windows_installer_version": WINDOWS_INSTALLER_VERSION,
    }
    assert len(commands) == 2


def test_alpha_09_nsis_qualification_refuses_existing_install_or_registration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_directory = tmp_path / "existing"
    install_directory.mkdir()
    with pytest.raises(ReleaseArtifactError, match="install directory already exists"):
        nsis_qualification.qualify_nsis_install_uninstall(
            release_directory=tmp_path / "missing-release",
            install_directory=install_directory,
        )

    install_directory.rmdir()
    monkeypatch.setattr(
        nsis_qualification,
        "_uninstall_registrations",
        lambda: (
            nsis_qualification.UninstallRegistration(
                hive="HKCU",
                key="existing",
                display_name="Sylvia GM",
                display_version="old",
                install_location="existing",
                uninstall_string="existing",
            ),
        ),
    )
    with pytest.raises(ReleaseArtifactError, match="existing Sylvia GM registration"):
        nsis_qualification.qualify_nsis_install_uninstall(
            release_directory=tmp_path / "missing-release",
            install_directory=install_directory,
        )

    monkeypatch.setattr(nsis_qualification, "_uninstall_registrations", tuple)
    monkeypatch.setattr(
        nsis_qualification,
        "_windows_host",
        lambda: {
            "architecture": "AMD64",
            "build": 19045,
            "major": 10,
            "minor": 0,
            "platform_version": "10.0.19045",
        },
    )
    with pytest.raises(ReleaseArtifactError, match="requires Windows 11"):
        nsis_qualification.qualify_nsis_install_uninstall(
            release_directory=tmp_path / "missing-release",
            install_directory=install_directory,
        )


def test_alpha_09_installed_owner_backup_reopens_and_recovers_cross_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_executable = tmp_path / "sylvia-gm-owner.exe"
    owner_executable.write_bytes(b"installed-owner")
    calls: list[tuple[str, str]] = []
    answers: list[str] = []
    campaign_id = "campaign-installed-qualification"
    archive_sha256 = "d" * 64

    class FakeOwnerProtocol:
        def __init__(self, *, executable, database, owner_id, timeout_seconds):
            assert executable == owner_executable
            assert owner_id.startswith("owner-installed-qualification")
            assert timeout_seconds == 120.0
            self.database = database
            database.parent.mkdir(parents=True, exist_ok=True)
            self.revision = 6 if database.parent.name != "installed-owner-a" else 3

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def request(self, _request_id, method, params=None):
            calls.append((self.database.parent.name, method))
            params = params or {}
            if method == "save_provider_settings":
                assert params["base_url"] == "http://127.0.0.1:9/v1"
                return {}
            if method == "model_choices":
                return [
                    {
                        "provider_id": "effective-custom",
                        "model_id": "effective/synthetic-installed-model",
                    }
                ]
            if method == "campaign_party_options":
                assert params["plan_id"] == "a-debt-in-iron"
                return {"player_archetypes": [{"id": "fixed-tavin", "campaign_ready": True}]}
            if method == "create_guided_campaign_draft":
                assert params["provider_id"] == "effective-custom"
                return {
                    "campaign_id": campaign_id,
                    "builder_session_id": "session-builder-installed",
                }
            if method == "advance_guided_campaign_draft":
                assert params["expected_construction_revision"] == len(answers)
                answers.append(params["current_step"])
                return {}
            if method == "start_guided_campaign":
                assert params["candidate_id"] == "sylvia.srd-lite.bg3"
                assert params["setting_id"] == "pneuma.severed-lands"
                assert params["plan_id"] == "a-debt-in-iron"
                assert params["player_character_id"] == "fixed-tavin"
                return {}
            if method == "finalize_campaign_construction":
                assert params["dm_model_id"] == "effective/synthetic-installed-model"
                return {
                    "handoff": {
                        "campaign_id": campaign_id,
                        "dm_session_id": "session-dm-installed",
                    },
                    "dm_turn_id": "turn-dm-installed",
                }
            if method == "snapshot":
                state = {"sylvia.srd-lite.bg3.entities": {}}
                if self.revision >= 4:
                    state["sylvia.srd-lite.bg3.entities"] = {
                        "installed-qualification-opponent": {"hp": 9}
                    }
                if self.revision >= 5:
                    state["sylvia.srd-lite.bg3.combat"] = {"encounter_active": True}
                return {
                    "revision": self.revision,
                    "participants": [
                        {"participant_id": "current-player-installed", "kind": "player_character"}
                    ],
                    "state": state,
                }
            if method == "execute_action":
                assert params["expected_revision"] == self.revision
                if params["tool_name"] == "prepare_srd_creature":
                    assert params["parameters"]["creature_id"] == "installed-qualification-opponent"
                    assert params["parameters"]["hit_points"] == 9
                    assert "synthetic" in params["parameters"]["source"]
                else:
                    assert params["tool_name"] == "start_combat_encounter_entities"
                    assert params["parameters"] == {
                        "first_actor": "current-player-installed",
                        "second_actor": "installed-qualification-opponent",
                        "first_surprised": False,
                        "second_surprised": False,
                        "tie_winner": "first",
                        "combat_scenes": [
                            nsis_qualification._qualification_combat_scene(
                                "current-player-installed"
                            )
                        ],
                    }
                self.revision += 1
                return {"status": "committed", "resulting_revision": self.revision}
            if method == "complete_turn":
                self.revision += 1
                return {"status": "completed"}
            if method == "backup_database":
                backup = self.database.parent / "backups/installed-backup.sqlite3"
                backup.parent.mkdir()
                backup.write_bytes(b"sqlite-backup")
                return {}
            if method == "export_campaign_file":
                archive = self.database.parent / "exports/installed-export.sylvia-gm-campaign"
                archive.parent.mkdir()
                archive.write_bytes(b"portable-campaign")
                return {
                    "export": {
                        "campaign_id": campaign_id,
                        "captured_revision": 6,
                        "archive_sha256": archive_sha256,
                    },
                    "artifact_path": str(archive),
                }
            if method == "recover_campaign_file":
                assert Path(params["source_path"]).read_bytes() == b"portable-campaign"
                return {
                    "campaign_id": campaign_id,
                    "captured_revision": 6,
                    "archive_sha256": archive_sha256,
                }
            if method == "campaign_index":
                return [{"campaign_id": campaign_id, "display_name": "Installed"}]
            raise AssertionError(f"unexpected owner method: {method}")

    monkeypatch.setattr(nsis_qualification, "_OwnerProtocol", FakeOwnerProtocol)
    receipt = nsis_qualification._qualify_installed_owner_backup_recovery(
        owner_executable=owner_executable, workspace=tmp_path, timeout_seconds=120.0
    )
    assert receipt == {
        "archive_sha256": archive_sha256,
        "backup_reopened": True,
        "captured_revision": 6,
        "portable_recovery": True,
        "combat_played": True,
        "plan_id": "a-debt-in-iron",
        "provider_inference": False,
    }
    assert answers == [
        "ruleset",
        "setting",
        "plan",
        "name",
        "participants",
        "boundaries_preferences",
        "specific_requests",
    ]
    assert [method for _, method in calls].count("execute_action") == 2
    assert [method for _, method in calls][-5:] == [
        "campaign_index",
        "snapshot",
        "recover_campaign_file",
        "campaign_index",
        "snapshot",
    ]
    assert not (tmp_path / "installed-owner-a").exists()
    assert not (tmp_path / "installed-owner-b").exists()
    assert not (tmp_path / "selected-recovery.sylvia-gm-campaign").exists()


def test_alpha_09_nsis_qualification_cleans_a_rejected_partial_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installer = tmp_path / "setup.exe"
    installer.write_bytes(b"installer")
    install_directory = tmp_path / "isolated-install"
    registrations: list[nsis_qualification.UninstallRegistration] = []
    calls: list[tuple[str, ...]] = []
    manifest = {
        "dependency_notices": _synthetic_release_notice_record(tmp_path / "release"),
        "product": {
            "name": "Sylvia GM",
            "version": PRERELEASE_VERSION,
            "windows_installer_version": WINDOWS_INSTALLER_VERSION,
        },
        "provenance": {"source_commit": "a" * 40, "source_tree": "b" * 40},
    }

    monkeypatch.setattr(
        nsis_qualification,
        "_manifest_nsis",
        lambda _release: (
            installer,
            {"kind": "nsis-installer", "name": installer.name, "sha256": "c" * 64},
            manifest,
        ),
    )
    monkeypatch.setattr(
        nsis_qualification,
        "_uninstall_registrations",
        lambda: tuple(registrations),
    )

    def run_checked(command: tuple[str, ...], *, timeout_seconds: float) -> None:
        assert timeout_seconds == 120.0
        calls.append(command)
        if command[0] == str(installer):
            install_directory.mkdir()
            (install_directory / "sylvia-gm.exe").write_bytes(b"app")
            (install_directory / "uninstall.exe").write_bytes(b"uninstaller")
            registrations.append(
                nsis_qualification.UninstallRegistration(
                    hive="HKCU",
                    key="isolated",
                    display_name="Sylvia GM",
                    display_version=WINDOWS_INSTALLER_VERSION,
                    install_location=str(install_directory),
                    uninstall_string=str(install_directory / "uninstall.exe"),
                )
            )
        else:
            for path in install_directory.iterdir():
                path.unlink()
            install_directory.rmdir()
            registrations.clear()

    monkeypatch.setattr(nsis_qualification, "_run_checked", run_checked)

    with pytest.raises(ReleaseArtifactError, match="omitted required payload"):
        nsis_qualification.qualify_nsis_install_uninstall(
            release_directory=tmp_path / "release",
            install_directory=install_directory,
        )

    assert not install_directory.exists()
    assert registrations == []
    assert calls == [
        (str(installer), "/S", f"/D={install_directory}"),
        (str(install_directory / "uninstall.exe"), "/S"),
    ]


def test_alpha_09_nsis_qualification_rejects_uninstaller_residue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installer = tmp_path / "setup.exe"
    installer.write_bytes(b"installer")
    install_directory = tmp_path / "isolated-install"
    registrations: list[nsis_qualification.UninstallRegistration] = []
    manifest = {
        "dependency_notices": _synthetic_release_notice_record(tmp_path / "release"),
        "product": {
            "name": "Sylvia GM",
            "version": PRERELEASE_VERSION,
            "windows_installer_version": WINDOWS_INSTALLER_VERSION,
        },
        "provenance": {"source_commit": "a" * 40, "source_tree": "b" * 40},
    }
    monkeypatch.setattr(
        nsis_qualification,
        "_manifest_nsis",
        lambda _release: (
            installer,
            {"kind": "nsis-installer", "name": installer.name, "sha256": "c" * 64},
            manifest,
        ),
    )
    monkeypatch.setattr(
        nsis_qualification,
        "_uninstall_registrations",
        lambda: tuple(registrations),
    )
    monkeypatch.setattr(
        nsis_qualification,
        "_qualify_installed_owner_backup_recovery",
        lambda **_kwargs: {
            "backup_reopened": True,
            "captured_revision": 4,
            "portable_recovery": True,
        },
    )
    monkeypatch.setattr(nsis_qualification.time, "sleep", lambda _seconds: None)

    def run_checked(command: tuple[str, ...], *, timeout_seconds: float) -> None:
        if command[0] != str(installer):
            return
        install_directory.mkdir()
        for name in ("sylvia-gm.exe", "sylvia-gm-owner.exe", "uninstall.exe"):
            (install_directory / name).write_bytes(name.encode("ascii"))
        _synthetic_release_notice_record(install_directory)
        registrations.append(
            nsis_qualification.UninstallRegistration(
                hive="HKCU",
                key="isolated",
                display_name="Sylvia GM",
                display_version=WINDOWS_INSTALLER_VERSION,
                install_location=str(install_directory),
                uninstall_string=str(install_directory / "uninstall.exe"),
            )
        )

    monkeypatch.setattr(nsis_qualification, "_run_checked", run_checked)

    with pytest.raises(ReleaseArtifactError, match="left its install directory"):
        nsis_qualification.qualify_nsis_install_uninstall(
            release_directory=tmp_path / "release",
            install_directory=install_directory,
            timeout_seconds=0.001,
        )


def test_alpha_09_msi_qualification_is_elevated_manifest_bound_and_removes_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    synthetic_notices: dict[str, object],
) -> None:
    installer = tmp_path / "synthetic.msi"
    nsis = tmp_path / "synthetic-setup.exe"
    installer.write_bytes(b"synthetic-msi")
    nsis.write_bytes(b"synthetic-nsis")
    release = tmp_path / "release"
    assemble_release_layout(
        **synthetic_notices,
        output=release,
        artifacts=(
            ReleaseArtifact(installer, "msi-installer"),
            ReleaseArtifact(nsis, "nsis-installer"),
        ),
        provenance={
            "source_commit": "a" * 40,
            "source_tree": "b" * 40,
            "source_timestamp_utc": "2026-08-11T00:00:00Z",
        },
        toolchain={
            "cargo": "cargo 1.97.0",
            "mcp": "2.0.0",
            "pyinstaller": "6.22.0",
            "python": "Python 3.11.15",
            "rustc": "rustc 1.97.0",
            "tauri_cli": "tauri-cli 2.8.0",
            "uv": "uv 0.11.6",
        },
        qualification={"windows_10": "pending", "windows_11": "pending"},
        license_path=ROOT / "LICENSE",
    )
    install_directory = tmp_path / "isolated-msi-install"
    registrations: list[nsis_qualification.UninstallRegistration] = []
    commands: list[tuple[str, ...]] = []
    msiexec = tmp_path / "msiexec.exe"
    msiexec.write_bytes(b"windows-installer")

    monkeypatch.setattr(msi_qualification, "_is_administrator", lambda: True)
    monkeypatch.setattr(msi_qualification, "_msiexec", lambda: msiexec)
    monkeypatch.setattr(
        msi_qualification,
        "_uninstall_registrations",
        lambda: tuple(registrations),
    )

    def run_checked(command: tuple[str, ...], *, timeout_seconds: float) -> None:
        assert timeout_seconds == 120.0
        commands.append(command)
        if command[1] == "/i":
            install_directory.mkdir()
            for name in ("sylvia-gm.exe", "sylvia-gm-owner.exe"):
                (install_directory / name).write_bytes(name.encode("ascii"))
            registrations.append(
                nsis_qualification.UninstallRegistration(
                    hive="HKLM",
                    key=r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{product}",
                    display_name="Sylvia GM",
                    display_version="0.3.0.1",
                    install_location=str(install_directory),
                    uninstall_string="MsiExec.exe /I{product}",
                )
            )
        else:
            assert command[1] == "/x"
            for path in install_directory.iterdir():
                path.unlink()
            install_directory.rmdir()
            registrations.clear()

    monkeypatch.setattr(msi_qualification, "_run_checked", run_checked)
    monkeypatch.setattr(
        msi_qualification,
        "_windows_host",
        lambda: {
            "architecture": "AMD64",
            "build": 26200,
            "major": 10,
            "minor": 0,
            "platform_version": "10.0.26200",
        },
    )
    monkeypatch.setattr(
        msi_qualification,
        "_qualify_installed_owner_backup_recovery",
        lambda *, owner_executable, workspace, timeout_seconds: {
            "backup_reopened": owner_executable == install_directory / "sylvia-gm-owner.exe",
            "captured_revision": 4,
            "portable_recovery": workspace.name.startswith("sylvia-gm-msi-owner-"),
        },
    )

    receipt = msi_qualification.qualify_msi_install_uninstall(
        release_directory=release,
        install_directory=install_directory,
        timeout_seconds=120.0,
    )

    assert receipt == {
        "application_executable": "sylvia-gm.exe",
        "host": {
            "architecture": "AMD64",
            "build": 26200,
            "major": 10,
            "minor": 0,
            "platform_version": "10.0.26200",
        },
        "install_directory_removed": True,
        "installer_kind": "msi-installer",
        "installer_name": installer.name,
        "installer_sha256": hashlib.sha256(b"synthetic-msi").hexdigest(),
        "installed_owner": {
            "backup_reopened": True,
            "captured_revision": 4,
            "portable_recovery": True,
        },
        "owner_executable": "sylvia-gm-owner.exe",
        "product": "Sylvia GM",
        "registration_hive": "HKLM",
        "registration_removed": True,
        "registration_version": "0.3.0.1",
        "source_commit": "a" * 40,
        "source_tree": "b" * 40,
        "version": PRERELEASE_VERSION,
        "windows_installer_version": WINDOWS_INSTALLER_VERSION,
    }
    assert commands == [
        (
            str(msiexec),
            "/i",
            str(release / installer.name),
            "/qn",
            "/norestart",
            f"INSTALLDIR={install_directory}",
        ),
        (
            str(msiexec),
            "/x",
            str(release / installer.name),
            "/qn",
            "/norestart",
        ),
    ]


def test_alpha_09_msi_qualification_refuses_non_elevated_or_existing_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_directory = tmp_path / "isolated-msi-install"
    monkeypatch.setattr(msi_qualification, "_is_administrator", lambda: False)
    with pytest.raises(ReleaseArtifactError, match="elevated administrator"):
        msi_qualification.qualify_msi_install_uninstall(
            release_directory=tmp_path / "missing-release",
            install_directory=install_directory,
        )

    monkeypatch.setattr(msi_qualification, "_is_administrator", lambda: True)
    monkeypatch.setattr(
        msi_qualification,
        "_uninstall_registrations",
        lambda: (
            nsis_qualification.UninstallRegistration(
                hive="HKLM",
                key="existing",
                display_name="Sylvia GM",
                display_version="0.3.0.1",
                install_location="existing",
                uninstall_string="existing",
            ),
        ),
    )
    with pytest.raises(ReleaseArtifactError, match="existing Sylvia GM registration"):
        msi_qualification.qualify_msi_install_uninstall(
            release_directory=tmp_path / "missing-release",
            install_directory=install_directory,
        )


def test_alpha_09_msi_qualification_cleans_a_rejected_partial_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installer = tmp_path / "setup.msi"
    installer.write_bytes(b"installer")
    install_directory = tmp_path / "isolated-msi-install"
    msiexec = tmp_path / "msiexec.exe"
    msiexec.write_bytes(b"windows-installer")
    registrations: list[nsis_qualification.UninstallRegistration] = []
    calls: list[tuple[str, ...]] = []
    manifest = {
        "product": {
            "name": "Sylvia GM",
            "version": PRERELEASE_VERSION,
            "windows_installer_version": WINDOWS_INSTALLER_VERSION,
        },
        "provenance": {"source_commit": "a" * 40, "source_tree": "b" * 40},
    }

    monkeypatch.setattr(msi_qualification, "_is_administrator", lambda: True)
    monkeypatch.setattr(msi_qualification, "_msiexec", lambda: msiexec)
    monkeypatch.setattr(
        msi_qualification,
        "_manifest_msi",
        lambda _release: (
            installer,
            {"kind": "msi-installer", "name": installer.name, "sha256": "c" * 64},
            manifest,
        ),
    )
    monkeypatch.setattr(
        msi_qualification,
        "_uninstall_registrations",
        lambda: tuple(registrations),
    )

    def run_checked(command: tuple[str, ...], *, timeout_seconds: float) -> None:
        assert timeout_seconds == 120.0
        calls.append(command)
        if command[1] == "/i":
            install_directory.mkdir()
            (install_directory / "sylvia-gm.exe").write_bytes(b"app")
            registrations.append(
                nsis_qualification.UninstallRegistration(
                    hive="HKLM",
                    key="isolated",
                    display_name="Sylvia GM",
                    display_version="0.3.0.1",
                    install_location=str(install_directory),
                    uninstall_string="MsiExec.exe /I{product}",
                )
            )
        else:
            for path in install_directory.iterdir():
                path.unlink()
            install_directory.rmdir()
            registrations.clear()

    monkeypatch.setattr(msi_qualification, "_run_checked", run_checked)

    with pytest.raises(ReleaseArtifactError, match="omitted required payload"):
        msi_qualification.qualify_msi_install_uninstall(
            release_directory=tmp_path / "release",
            install_directory=install_directory,
        )

    assert not install_directory.exists()
    assert registrations == []
    assert [command[1] for command in calls] == ["/i", "/x"]


@pytest.fixture
def notice_release(tmp_path: Path, synthetic_notices: dict[str, object]) -> Path:
    installer = tmp_path / "synthetic-notice-setup.exe"
    installer.write_bytes(b"synthetic-installer-with-notices")
    release = tmp_path / "release"
    assemble_release_layout(
        **synthetic_notices,
        output=release,
        artifacts=(ReleaseArtifact(installer, "nsis-installer"),),
        provenance={
            "source_commit": "a" * 40,
            "source_tree": "b" * 40,
            "source_timestamp_utc": "2026-08-11T00:00:00Z",
        },
        toolchain={
            "cargo": "synthetic-cargo",
            "mcp": "synthetic-mcp",
            "pyinstaller": "synthetic-pyinstaller",
            "python": "synthetic-python",
            "rustc": "synthetic-rustc",
            "tauri_cli": "synthetic-tauri",
            "uv": "synthetic-uv",
        },
        qualification={"windows_10": "pending", "windows_11": "pending"},
        license_path=ROOT / "LICENSE",
    )
    return release


@pytest.mark.parametrize("content", [b"", b" \r\n\t", b"\xef\xbb\xbf", b"text\x00", b"\xff"])
def test_alpha_09_notice_input_rejects_empty_or_unreadable_text(tmp_path: Path, content: bytes):
    path = tmp_path / "synthetic-empty-or-unreadable-notices.txt"
    path.write_bytes(content)
    with pytest.raises(ReleaseArtifactError, match="empty|UTF-8"):
        read_dependency_notices(path, expected_sha256=hashlib.sha256(content).hexdigest())


@pytest.mark.parametrize("digest", ["0" * 64, "not-a-sha256", "A" * 64])
def test_alpha_09_notice_input_rejects_wrong_or_invalid_pin(tmp_path: Path, digest: str):
    path = tmp_path / "synthetic-notices.txt"
    path.write_bytes(SYNTHETIC_NOTICES)
    with pytest.raises(ReleaseArtifactError, match="pin"):
        read_dependency_notices(path, expected_sha256=digest)


@pytest.mark.parametrize("mutation", ["missing", "empty", "wrong", "directory"])
def test_alpha_09_release_layout_and_qualified_copy_reject_bad_notice_bytes(
    tmp_path: Path,
    notice_release: Path,
    mutation: str,
):
    notice = notice_release / DEPENDENCY_NOTICES_PATH
    notice.unlink()
    if mutation == "empty":
        notice.write_bytes(b" \r\n")
    elif mutation == "wrong":
        notice.write_bytes(b"Wrong synthetic document\n")
    elif mutation == "directory":
        notice.mkdir()
    with pytest.raises(ReleaseArtifactError, match="Dependency|dependency"):
        verify_release_layout(notice_release)
    qualified = tmp_path / "qualified"
    with pytest.raises(ReleaseArtifactError, match="Dependency|dependency"):
        assemble_qualified_release_layout(
            source=notice_release,
            output=qualified,
            qualification={"windows_10": "not_available", "windows_11": "passed"},
        )
    assert not qualified.exists()


@pytest.mark.parametrize(
    "extra, directory",
    [
        ("extra.txt", False),
        ("extra", True),
        ("licenses/extra.txt", False),
        ("licenses/extra", True),
    ],
)
def test_alpha_09_release_layout_rejects_unselected_files_and_directories(
    notice_release: Path,
    extra: str,
    directory: bool,
):
    path = notice_release / extra
    if directory:
        path.mkdir()
    else:
        path.write_bytes(b"unselected synthetic data")
    with pytest.raises(ReleaseArtifactError, match="undeclared"):
        verify_release_layout(notice_release)


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "../dependency-notices.txt",
        "licenses/../dependency-notices.txt",
        "licenses/other.txt",
        "C:/dependency-notices.txt",
        "licenses\\dependency-notices.txt",
    ],
)
def test_alpha_09_release_layout_rejects_notice_path_substitutions(
    notice_release: Path,
    unsafe_path: str,
):
    manifest_path = notice_release / "release-manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["dependency_notices"]["path"] = unsafe_path
    manifest_path.write_bytes(
        (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()
    )
    with pytest.raises(ReleaseArtifactError, match="notice path"):
        verify_release_layout(notice_release)


def test_alpha_09_notice_input_rejects_missing_file_and_redirected_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    with pytest.raises(ReleaseArtifactError, match="cannot be read"):
        read_dependency_notices(tmp_path / "missing.txt")
    destination = tmp_path / "synthetic-target.txt"
    destination.write_bytes(SYNTHETIC_NOTICES)
    substituted = tmp_path / "redirected" / "synthetic-target.txt"
    resolve = Path.resolve

    def redirected_resolve(path: Path, *args, **kwargs):
        if path == substituted:
            return destination
        return resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", redirected_resolve)
    with pytest.raises(ReleaseArtifactError, match="unredirected"):
        read_dependency_notices(substituted)


@pytest.mark.parametrize(
    "failure, message",
    [
        ("initial-missing", "omitted required payload"),
        ("initial-wrong", "do not match the pinned release"),
        ("transient-only", "omitted required payload"),
        ("post-exit-missing", "cannot be read"),
        ("post-exit-wrong", "do not match the pinned release"),
        ("post-exit-unreadable", "cannot be read"),
    ],
)
def test_alpha_09_nsis_notice_qualification_requires_persistence_after_owner_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    notice_release: Path,
    failure: str,
    message: str,
):
    target = tmp_path / "isolated-install"
    notice = target / DEPENDENCY_NOTICES_PATH
    registrations = []
    events = []
    owner_exited = False
    installer = notice_release / "synthetic-notice-setup.exe"
    original_read_bytes = Path.read_bytes
    original_notice_check = nsis_qualification._require_installed_dependency_notices

    def read_bytes(path: Path):
        if path == notice and owner_exited and failure == "post-exit-unreadable":
            raise PermissionError("synthetic post-exit unreadability")
        return original_read_bytes(path)

    def notice_check(directory, expected):
        events.append("read-after-exit" if owner_exited else "read-before-launch")
        original_notice_check(directory, expected)

    def run_checked(command, *, timeout_seconds):
        if command[0] == str(installer):
            target.mkdir()
            for name in ("sylvia-gm.exe", "sylvia-gm-owner.exe", "uninstall.exe"):
                (target / name).write_bytes(b"synthetic executable - never run")
            if failure not in {"initial-missing", "transient-only"}:
                notice.parent.mkdir()
                notice.write_bytes(
                    b"wrong synthetic input" if failure == "initial-wrong" else SYNTHETIC_NOTICES
                )
            if failure == "transient-only":
                _synthetic_release_notice_record(tmp_path / "synthetic-onefile-extraction")
            registrations.append(
                nsis_qualification.UninstallRegistration(
                    hive="HKCU",
                    key="synthetic",
                    display_name="Sylvia GM",
                    display_version=WINDOWS_INSTALLER_VERSION,
                    install_location=str(target),
                    uninstall_string=str(target / "uninstall.exe"),
                )
            )
        else:
            events.append("uninstall")
            shutil.rmtree(target)
            registrations.clear()

    def qualify_owner(**_kwargs):
        nonlocal owner_exited
        events.extend(["owner-launch", "owner-exit"])
        owner_exited = True
        if failure == "post-exit-missing":
            notice.unlink()
        elif failure == "post-exit-wrong":
            notice.write_bytes(b"changed synthetic notice after exit")
        return {"backup_reopened": True, "portable_recovery": True}

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr(nsis_qualification, "_windows_host", lambda: {"build": 26200})
    monkeypatch.setattr(
        nsis_qualification, "_uninstall_registrations", lambda: tuple(registrations)
    )
    monkeypatch.setattr(nsis_qualification, "_run_checked", run_checked)
    monkeypatch.setattr(nsis_qualification, "_require_installed_dependency_notices", notice_check)
    monkeypatch.setattr(
        nsis_qualification, "_qualify_installed_owner_backup_recovery", qualify_owner
    )
    with pytest.raises(ReleaseArtifactError, match=message):
        nsis_qualification.qualify_nsis_install_uninstall(
            release_directory=notice_release,
            install_directory=target,
        )
    assert not target.exists()
    assert not registrations
    assert events[-1] == "uninstall"
    if failure.startswith("post-exit"):
        assert events == [
            "read-before-launch",
            "owner-launch",
            "owner-exit",
            "read-after-exit",
            "uninstall",
        ]
    else:
        assert "owner-launch" not in events


@pytest.mark.parametrize("changed_during_build", [False, True])
def test_alpha_09_windows_builder_requires_cli_pin_and_preserves_notice_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    notice_release: Path,
    changed_during_build: bool,
):
    with pytest.raises(SystemExit):
        windows_builder._parser(tmp_path).parse_args([])
    digest = hashlib.sha256(SYNTHETIC_NOTICES).hexdigest()
    arguments = windows_builder._parser(tmp_path).parse_args(
        [
            "--dependency-notices-sha256",
            digest,
            "--forbidden-name-file",
            str(tmp_path / "external-names.txt"),
        ]
    )
    assert arguments.dependency_notices_sha256 == digest

    root = tmp_path / "synthetic-source"
    (root / "src-tauri").mkdir(parents=True)
    shutil.copyfile(ROOT / "src-tauri/tauri.conf.json", root / "src-tauri/tauri.conf.json")
    shutil.copyfile(ROOT / "LICENSE", root / "LICENSE")
    (root / "licenses").mkdir()
    for name in windows_builder.RUST_STDLIB_NOTICE_SHA256:
        shutil.copyfile(ROOT / name, root / name)
    notice = root / DEPENDENCY_NOTICES_SOURCE
    notice.write_bytes(SYNTHETIC_NOTICES)
    reference = verify_release_layout(notice_release)
    commands = []

    def run(command, *, cwd, capture=False, env=None):
        commands.append(tuple(command))
        if tuple(command) == TAURI_BUILD_COMMAND:
            assert env["UV_PYTHON"] == windows_builder.sys.executable
            flags = env["CARGO_ENCODED_RUSTFLAGS"].split("\x1f")
            assert f"--remap-path-prefix={root}=/sylvia/source" in flags
            assert f"--remap-path-prefix={Path.home()}=/build-home" in flags
            assert "link-arg=/PDBALTPATH:%_PDB%" in flags
            target = Path(env["CARGO_TARGET_DIR"])
            assert target.is_relative_to(root / ".owner-build")
            bundle = target / "release/bundle/nsis"
            bundle.mkdir(parents=True)
            (bundle / "synthetic-build-setup.exe").write_bytes(b"synthetic build - never execute")
            if changed_during_build:
                notice.write_bytes(b"changed synthetic build-time document")
        return ""

    monkeypatch.setattr(windows_builder, "_run", run)
    monkeypatch.setattr(
        windows_builder, "validate_version_sources", lambda _root: PRERELEASE_VERSION
    )
    monkeypatch.setattr(windows_builder, "_require_clean_tree", lambda _root: None)
    monkeypatch.setattr(windows_builder, "_source_identity", lambda _root: reference["provenance"])
    monkeypatch.setattr(windows_builder, "_toolchain", lambda _root: reference["toolchain"])
    monkeypatch.setattr(windows_builder, "check_source", lambda *_: {"passed": True})
    monkeypatch.setattr(windows_builder, "_source_input_manifest", lambda _: {"LICENSE": "a" * 64})
    from tests.test_portable_release_recipe import payloads

    monkeypatch.setattr(windows_builder, "portable_payloads", lambda *_args, **_kwargs: payloads())
    retained = root / "src-tauri/target/release/bundle/nsis/retained.exe"
    retained.parent.mkdir(parents=True)
    retained.write_bytes(b"older retained build")
    output = tmp_path / "synthetic-built-release"
    if changed_during_build:
        with pytest.raises(ReleaseArtifactError, match="pin"):
            windows_builder.build_windows_prerelease(
                root=root,
                output=output,
                windows_10="not_available",
                windows_11="pending",
                dependency_notices_sha256=digest,
                forbidden_name_file=tmp_path / "external-names.txt",
            )
        assert not output.exists()
        return
    manifest = windows_builder.build_windows_prerelease(
        root=root,
        output=output,
        windows_10="not_available",
        windows_11="pending",
        dependency_notices_sha256=digest,
        forbidden_name_file=tmp_path / "external-names.txt",
    )
    assert (output / DEPENDENCY_NOTICES_PATH).read_bytes() == SYNTHETIC_NOTICES
    assert verify_release_layout(output) == manifest
    assert {entry["kind"] for entry in manifest["artifacts"]} == {
        "nsis-installer",
        "portable-exe-zip",
    }
    assert retained.read_bytes() == b"older retained build"
    assert commands == [*LOCK_CHECK_COMMANDS, TAURI_BUILD_COMMAND]

    notice.write_bytes(b"changed synthetic input")
    commands.clear()
    with pytest.raises(ReleaseArtifactError, match="pin"):
        windows_builder.build_windows_prerelease(
            root=root,
            output=tmp_path / "rejected",
            windows_10="pending",
            windows_11="pending",
            dependency_notices_sha256=digest,
        )
    assert commands == []


@pytest.mark.parametrize("failure", ["missing", "empty", "wrong", "missing-pin", "substitution"])
def test_alpha_09_windows_builder_rejects_notice_input_before_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
):
    (tmp_path / "src-tauri").mkdir()
    resources = {f"../{DEPENDENCY_NOTICES_SOURCE}": DEPENDENCY_NOTICES_PATH}
    if failure == "substitution":
        resources = {"../other-notices.txt": DEPENDENCY_NOTICES_PATH}
    (tmp_path / "src-tauri/tauri.conf.json").write_text(
        json.dumps({"bundle": {"resources": resources}}),
        encoding="utf-8",
    )
    path = tmp_path / DEPENDENCY_NOTICES_SOURCE
    if failure != "missing":
        path.write_bytes(b"" if failure == "empty" else SYNTHETIC_NOTICES)
    digest = hashlib.sha256(SYNTHETIC_NOTICES).hexdigest()
    if failure == "wrong":
        digest = "0" * 64
    elif failure == "missing-pin":
        digest = None

    def forbidden_execution(*_args, **_kwargs):
        pytest.fail("Rejected notice input reached a build/source command")

    monkeypatch.setattr(windows_builder, "_run", forbidden_execution)
    monkeypatch.setattr(windows_builder, "validate_version_sources", forbidden_execution)
    with pytest.raises(ReleaseArtifactError):
        windows_builder.build_windows_prerelease(
            root=tmp_path,
            output=tmp_path / "rejected",
            windows_10="pending",
            windows_11="pending",
            dependency_notices_sha256=digest,
        )
    assert not (tmp_path / "rejected").exists()


@pytest.fixture
def rust_notice_release(tmp_path: Path, synthetic_notices: dict[str, object]) -> Path:
    installer = tmp_path / "DO-NOT-RUN-Companion-Fixture.exe"
    installer.write_bytes(b"SYNTHETIC CONTRACT FIXTURE ONLY")
    release = tmp_path / "companion-release"
    assemble_release_layout(
        **synthetic_notices,
        output=release,
        artifacts=[ReleaseArtifact(installer, "nsis-installer")],
        provenance={
            "source_commit": "a" * 40,
            "source_tree": "b" * 40,
            "source_timestamp_utc": "2026-10-16T00:00:00Z",
        },
        toolchain=dict.fromkeys(
            ["cargo", "mcp", "pyinstaller", "python", "rustc", "tauri_cli", "uv"],
            "synthetic fixture",
        ),
        qualification={"windows_10": "pending", "windows_11": "pending"},
        license_path=ROOT / "LICENSE",
        dependency_notice_companions_directory=ROOT / "licenses",
    )
    return release


def test_rust_notice_companions_survive_qualification_copy(
    tmp_path: Path,
    rust_notice_release: Path,
) -> None:
    manifest = verify_release_layout(rust_notice_release)
    assert manifest["format_version"] == 3
    qualified = tmp_path / "qualified-companions"
    result = assemble_qualified_release_layout(
        source=rust_notice_release,
        output=qualified,
        qualification={"windows_10": "not_available", "windows_11": "pending"},
    )
    assert verify_release_layout(qualified) == result
    records = manifest["dependency_notices"]["companions"]
    assert len(records) == 4
    for row in records:
        assert (qualified / row["path"]).read_bytes() == (ROOT / row["path"]).read_bytes()


@pytest.mark.parametrize(
    "name",
    [
        "RUST-STDLIB-COPYRIGHT",
        "RUST-STDLIB-COPYRIGHT-library.html",
        "RUST-STDLIB-LICENSE-APACHE",
        "RUST-STDLIB-LICENSE-MIT",
    ],
)
@pytest.mark.parametrize("mutation", ["missing", "changed"])
def test_rust_notice_layout_rejects_each_missing_or_changed_companion(
    tmp_path: Path,
    rust_notice_release: Path,
    name: str,
    mutation: str,
) -> None:
    target = rust_notice_release / "licenses" / name
    if mutation == "missing":
        target.unlink()
    else:
        target.write_bytes(target.read_bytes() + b"altered")
    with pytest.raises(ReleaseArtifactError):
        verify_release_layout(rust_notice_release)
    with pytest.raises(ReleaseArtifactError):
        assemble_qualified_release_layout(
            source=rust_notice_release,
            output=tmp_path / "rejected-copy",
            qualification={"windows_10": "pending", "windows_11": "pending"},
        )
    assert not (tmp_path / "rejected-copy").exists()


@pytest.mark.parametrize(
    "mutation", ["missing-file", "changed-file", "missing-mapping", "duplicate-mapping"]
)
def test_rust_notice_builder_rejects_companion_gap_before_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    (tmp_path / "src-tauri").mkdir()
    (tmp_path / "licenses").mkdir()
    resources = json.loads((ROOT / "src-tauri/tauri.conf.json").read_bytes())["bundle"]["resources"]
    for name in windows_builder.RUST_STDLIB_NOTICE_SHA256:
        shutil.copyfile(ROOT / name, tmp_path / name)
    name = next(iter(windows_builder.RUST_STDLIB_NOTICE_SHA256))
    if mutation == "missing-file":
        (tmp_path / name).unlink()
    elif mutation == "changed-file":
        (tmp_path / name).write_bytes(b"wrong original")
    elif mutation == "missing-mapping":
        del resources["../" + name]
    else:
        resources["../substitute"] = name
    (tmp_path / "src-tauri/tauri.conf.json").write_text(
        json.dumps({"bundle": {"resources": resources}})
    )
    (tmp_path / DEPENDENCY_NOTICES_SOURCE).write_bytes(SYNTHETIC_NOTICES)

    def forbidden(*args, **kwargs):
        pytest.fail("Incomplete companion set reached a build/source command")

    monkeypatch.setattr(windows_builder, "_run", forbidden)
    monkeypatch.setattr(windows_builder, "validate_version_sources", forbidden)
    with pytest.raises(ReleaseArtifactError):
        windows_builder.build_windows_prerelease(
            root=tmp_path,
            output=tmp_path / "rejected",
            windows_10="pending",
            windows_11="pending",
            dependency_notices_sha256=hashlib.sha256(SYNTHETIC_NOTICES).hexdigest(),
        )
    assert not (tmp_path / "rejected").exists()
