"""NSIS target/registration ownership regressions; no executable is run."""

from __future__ import annotations

import dataclasses
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts import qualify_windows_nsis as runner

from sylvia_gm.release_artifact import (
    DEPENDENCY_NOTICES_PATH,
    WINDOWS_INSTALLER_VERSION,
    ReleaseArtifactError,
)


@pytest.fixture(autouse=True)
def no_host_install_or_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any escape from the mocked process/registry boundary a test failure."""

    def denied(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Real process/registry access is forbidden in these fixtures")

    monkeypatch.setattr(subprocess, "run", denied)
    monkeypatch.setattr(subprocess, "Popen", denied)
    monkeypatch.setattr(runner, "_uninstall_registrations", denied)
    monkeypatch.setattr(runner, "_windows_host", lambda: {"build": 26200})
    monkeypatch.setitem(sys.modules, "winreg", None)


def registration(target: Path, **changes: str) -> runner.UninstallRegistration:
    return dataclasses.replace(
        runner.UninstallRegistration(
            hive="HKCU",
            key=r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Sylvia GM",
            display_name="Sylvia GM",
            display_version=WINDOWS_INSTALLER_VERSION,
            install_location=str(target),
            uninstall_string=f'"{target / "uninstall.exe"}"',
        ),
        **changes,
    )


@pytest.mark.parametrize(
    "case",
    [
        "exact",
        "quoted-arguments",
        "trailing-separator",
        "case-and-slashes",
        "sibling-location",
        "foreign-uninstaller",
        "command-wrapper",
        "executable-suffix",
        "quoted-suffix",
        "missing-location",
        "missing-command",
        "relative-command",
        "wrong-version",
        "wrong-product",
        "unquoted-spaces",
    ],
)
def test_registration_requires_both_exact_target_fields(tmp_path: Path, case: str) -> None:
    target = tmp_path / "Owned App"
    row = registration(target)
    accepted = case in {"exact", "quoted-arguments", "trailing-separator", "case-and-slashes"}
    changes = {
        "quoted-arguments": {"uninstall_string": row.uninstall_string + " /S"},
        "trailing-separator": {"install_location": str(target) + "/"},
        "case-and-slashes": {
            "install_location": str(target).swapcase().replace("\\", "/"),
            "uninstall_string": row.uninstall_string.swapcase().replace("\\", "/"),
        },
        "sibling-location": {"install_location": str(target.with_name(target.name + "-sibling"))},
        "foreign-uninstaller": {"uninstall_string": f'"{tmp_path / "foreign/uninstall.exe"}"'},
        "command-wrapper": {
            "install_location": "",
            "uninstall_string": "cmd.exe /c " + row.uninstall_string,
        },
        "executable-suffix": {
            "install_location": "",
            "uninstall_string": f'"{target / "uninstall.exe.bak"}"',
        },
        "quoted-suffix": {"uninstall_string": row.uninstall_string + ".bak"},
        "missing-location": {"install_location": ""},
        "missing-command": {"uninstall_string": ""},
        "relative-command": {"uninstall_string": "uninstall.exe"},
        "wrong-version": {"display_version": "unrelated-version"},
        "wrong-product": {"display_name": "Unrelated app"},
        "unquoted-spaces": {"uninstall_string": str(target / "uninstall.exe")},
    }
    row = dataclasses.replace(row, **changes.get(case, {}))
    assert runner._registration_matches(row, target) is accepted


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Represent install, registry and process results with disposable fixture files."""
    target = tmp_path / "owned-install"
    release = tmp_path / "release"
    release.mkdir()
    installer = release / "synthetic-setup.exe"
    installer.write_bytes(b"NOT AN EXECUTABLE: fixture only")
    notices = b"SYNTHETIC TEST FIXTURE ONLY - no shipping licence text.\n"
    notice_path = release / DEPENDENCY_NOTICES_PATH
    notice_path.parent.mkdir(parents=True)
    notice_path.write_bytes(notices)
    state = SimpleNamespace(
        target=target,
        release=release,
        installer=installer,
        registrations=[],
        calls=[],
        owner_calls=[],
        after_install=None,
        owner_action=None,
        after_uninstall=None,
    )
    manifest = {
        "dependency_notices": {"sha256": hashlib.sha256(notices).hexdigest()},
        "product": {
            "name": "Sylvia GM",
            "version": "fixture",
            "windows_installer_version": WINDOWS_INSTALLER_VERSION,
        },
        "provenance": {"source_commit": "a" * 40, "source_tree": "b" * 40},
    }

    def process(command: tuple[str, ...], *, timeout_seconds: float) -> None:
        state.calls.append(command)
        if command[0] == str(installer):
            assert command == (str(installer), "/S", f"/D={target}")
            target.mkdir()
            for name in ("sylvia-gm.exe", "sylvia-gm-owner.exe", "uninstall.exe"):
                (target / name).write_bytes(b"fixture only")
            installed = target / DEPENDENCY_NOTICES_PATH
            installed.parent.mkdir(parents=True)
            installed.write_bytes(notices)
            state.registrations.append(registration(target))
            if state.after_install:
                state.after_install()
        else:
            assert command == (str(target / "uninstall.exe"), "/S")
            assert target.parent == tmp_path
            shutil.rmtree(target)
            state.registrations.clear()
            if state.after_uninstall:
                state.after_uninstall()

    def owner(**kwargs: object) -> dict[str, bool]:
        state.owner_calls.append(kwargs)
        if state.owner_action:
            state.owner_action()
        return {"backup_reopened": True}

    monkeypatch.setattr(
        runner,
        "_manifest_nsis",
        lambda _release: (installer, {"name": installer.name, "sha256": "c" * 64}, manifest),
    )
    monkeypatch.setattr(runner, "_uninstall_registrations", lambda: tuple(state.registrations))
    monkeypatch.setattr(runner, "_run_checked", process)
    monkeypatch.setattr(runner, "_qualify_installed_owner_backup_recovery", owner)
    state.run = lambda: runner.qualify_nsis_install_uninstall(
        release_directory=release, install_directory=target, timeout_seconds=0.01
    )
    return state


def test_successful_owned_cleanup_invokes_only_target(world: SimpleNamespace) -> None:
    receipt = world.run()
    assert receipt["install_directory_removed"] and receipt["registration_removed"]
    assert len(world.owner_calls) == 1
    assert world.calls == [
        (str(world.installer), "/S", f"/D={world.target}"),
        (str(world.target / "uninstall.exe"), "/S"),
    ]


@pytest.mark.parametrize("failure", ["version", "location", "uninstaller", "duplicate", "absent"])
def test_failed_registration_validation_never_uninstalls(
    world: SimpleNamespace, failure: str
) -> None:
    def corrupt() -> None:
        row = world.registrations[0]
        if failure == "duplicate":
            world.registrations.append(dataclasses.replace(row, key="new-unrelated-key"))
        elif failure == "absent":
            world.registrations.clear()
        else:
            field, value = {
                "version": ("display_version", "wrong"),
                "location": ("install_location", str(world.target) + "-sibling"),
                "uninstaller": (
                    "uninstall_string",
                    str(world.target.parent / "foreign/uninstall.exe"),
                ),
            }[failure]
            world.registrations[0] = dataclasses.replace(row, **{field: value})

    world.after_install = corrupt
    with pytest.raises(ReleaseArtifactError):
        world.run()
    assert len(world.calls) == 1
    assert world.target.is_dir() and (world.target / "uninstall.exe").is_file()
    assert world.owner_calls == []


@pytest.mark.parametrize("change", ["new-key", "new-hive", "new-field", "extra", "missing"])
def test_changed_registration_before_cleanup_is_retained(
    world: SimpleNamespace, change: str
) -> None:
    def alter() -> None:
        row = world.registrations[0]
        if change == "extra":
            world.registrations.append(dataclasses.replace(row, key="new-unrelated-key"))
        elif change == "missing":
            world.registrations.clear()
        else:
            field, value = {
                "new-key": ("key", "new-unrelated-key"),
                "new-hive": ("hive", "HKLM"),
                "new-field": (
                    "uninstall_string",
                    str(world.target.parent / "foreign/uninstall.exe"),
                ),
            }[change]
            world.registrations[0] = dataclasses.replace(row, **{field: value})

    world.owner_action = alter
    with pytest.raises(ReleaseArtifactError):
        world.run()
    assert len(world.calls) == 1
    assert world.target.is_dir()


def test_owned_cleanup_still_runs_after_owner_check_fails(world: SimpleNamespace) -> None:
    def fail() -> None:
        raise ReleaseArtifactError("synthetic owner failure")

    world.owner_action = fail
    with pytest.raises(ReleaseArtifactError, match="synthetic owner failure"):
        world.run()
    assert len(world.calls) == 2 and not world.target.exists()


def test_failed_installer_without_validated_registration_is_not_cleaned(
    world: SimpleNamespace,
) -> None:
    def fail() -> None:
        raise ReleaseArtifactError("synthetic installer failure")

    world.after_install = fail
    with pytest.raises(ReleaseArtifactError):
        world.run()
    assert len(world.calls) == 1 and world.target.is_dir()


def test_failed_validation_is_not_retried_as_cleanup_authority(
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checks: list[runner.UninstallRegistration] = []
    original = runner._registration_matches

    def first_rejected(row: runner.UninstallRegistration, target: Path) -> bool:
        checks.append(row)
        return len(checks) > 1 and original(row, target)

    monkeypatch.setattr(runner, "_registration_matches", first_rejected)
    with pytest.raises(ReleaseArtifactError):
        world.run()
    assert len(checks) == 1 and len(world.calls) == 1 and world.target.is_dir()


def test_redirected_uninstaller_before_cleanup_is_not_executed(
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = Path.is_symlink

    def redirect() -> None:
        monkeypatch.setattr(
            Path,
            "is_symlink",
            lambda path: path == world.target / "uninstall.exe" or original(path),
        )

    world.owner_action = redirect
    with pytest.raises(ReleaseArtifactError, match="uninstaller is missing"):
        world.run()
    assert len(world.calls) == 1 and world.target.is_dir()


def test_preexisting_registration_is_refused_before_installer(world: SimpleNamespace) -> None:
    world.registrations.append(registration(world.target.parent / "foreign"))
    with pytest.raises(ReleaseArtifactError, match="existing Sylvia GM registration"):
        world.run()
    assert world.calls == [] and world.owner_calls == [] and not world.target.exists()


def test_preexisting_target_is_untouched(world: SimpleNamespace) -> None:
    world.target.mkdir()
    marker = world.target / "existing-data"
    marker.write_bytes(b"preserve")
    with pytest.raises(ReleaseArtifactError, match="install directory already exists"):
        world.run()
    assert marker.read_bytes() == b"preserve" and world.calls == []


def test_redirected_target_before_cleanup_is_not_executed(
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = Path.resolve

    def redirect() -> None:
        def resolve(path: Path, *args: object, **kwargs: object) -> Path:
            if path == world.target:
                return world.target.parent / "foreign"
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "resolve", resolve)

    world.owner_action = redirect
    with pytest.raises(ReleaseArtifactError):
        world.run()
    assert len(world.calls) == 1 and world.target.is_dir()


def test_unrelated_registration_after_uninstall_is_never_cleaned(world: SimpleNamespace) -> None:
    foreign = world.target.parent / "foreign"
    foreign.mkdir()
    marker = foreign / "preserve"
    marker.write_bytes(b"unrelated")
    world.after_uninstall = lambda: world.registrations.append(registration(foreign))
    with pytest.raises(ReleaseArtifactError, match="left its install directory"):
        world.run()
    assert len(world.calls) == 2 and marker.read_bytes() == b"unrelated"
    assert len(world.registrations) == 1
