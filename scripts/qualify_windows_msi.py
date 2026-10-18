"""Qualify one manifest-bound elevated MSI install/uninstall on Windows."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING or __package__:
    from scripts.qualify_windows_nsis import (
        UninstallRegistration,
        _qualify_installed_owner_backup_recovery,
        _run_checked,
        _uninstall_registrations,
        _windows_host,
    )
else:
    from qualify_windows_nsis import (
        UninstallRegistration,
        _qualify_installed_owner_backup_recovery,
        _run_checked,
        _uninstall_registrations,
        _windows_host,
    )

from sylvia_gm.release_artifact import (
    WINDOWS_INSTALLER_VERSION,
    ReleaseArtifactError,
    verify_release_layout,
)

_PRODUCT_NAME = "Sylvia GM"
_MSI_REGISTRATION_VERSION = WINDOWS_INSTALLER_VERSION.replace("-", ".")
_REQUIRED_PAYLOAD = ("sylvia-gm.exe", "sylvia-gm-owner.exe")


def _is_administrator() -> bool:
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def _msiexec() -> Path:
    executable = shutil.which("msiexec.exe")
    if executable is None:
        raise ReleaseArtifactError("Windows Installer is unavailable")
    return Path(executable).resolve(strict=True)


def _manifest_msi(
    release_directory: Path,
) -> tuple[Path, dict[str, object], dict[str, object]]:
    manifest = verify_release_layout(release_directory)
    records = manifest["artifacts"]
    if not isinstance(records, list):
        raise ReleaseArtifactError("Release manifest artifact list is invalid")
    matches = [record for record in records if record.get("kind") == "msi-installer"]
    if len(matches) != 1:
        raise ReleaseArtifactError("Release manifest has no unique MSI installer")
    record = matches[0]
    name = record.get("name")
    if not isinstance(name, str):
        raise ReleaseArtifactError("Release MSI installer name is invalid")
    return release_directory.resolve(strict=True) / name, record, manifest


def _require_new_install_directory(install_directory: Path) -> Path:
    supplied = Path(install_directory)
    if supplied.exists() or supplied.is_symlink():
        raise ReleaseArtifactError("Isolated install directory already exists")
    try:
        parent = supplied.parent.resolve(strict=True)
    except OSError as error:
        raise ReleaseArtifactError("Isolated install parent cannot be resolved") from error
    if not parent.is_dir() or parent.is_symlink() or not supplied.name:
        raise ReleaseArtifactError("Isolated install parent is unsafe")
    resolved = (parent / supplied.name).resolve()
    if resolved.parent != parent:
        raise ReleaseArtifactError("Isolated install directory escapes its parent")
    return resolved


def _require_payload(install_directory: Path) -> None:
    if not install_directory.is_dir() or install_directory.is_symlink():
        raise ReleaseArtifactError("MSI installer did not create the isolated directory")
    for name in _REQUIRED_PAYLOAD:
        path = install_directory / name
        if not path.is_file() or path.is_symlink():
            raise ReleaseArtifactError(f"MSI installer omitted required payload: {name}")


def _registration_matches(registration: UninstallRegistration) -> bool:
    return (
        registration.hive == "HKLM"
        and registration.display_name.casefold() == _PRODUCT_NAME.casefold()
        and registration.display_version == _MSI_REGISTRATION_VERSION
    )


def _wait_until_removed(install_directory: Path, *, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        if not install_directory.exists() and not _uninstall_registrations():
            return
        if time.monotonic() >= deadline:
            raise ReleaseArtifactError(
                "MSI uninstall left its install directory or Windows registration behind"
            )
        time.sleep(0.1)


def qualify_msi_install_uninstall(
    *,
    release_directory: Path,
    install_directory: Path,
    timeout_seconds: float = 120.0,
) -> dict[str, object]:
    """Install and remove the exact elevated MSI without touching an existing install."""

    if timeout_seconds <= 0:
        raise ReleaseArtifactError("MSI qualification timeout must be positive")
    target = _require_new_install_directory(install_directory)
    if not _is_administrator():
        raise ReleaseArtifactError("MSI qualification requires an elevated administrator process")
    if _uninstall_registrations():
        raise ReleaseArtifactError(
            "MSI qualification refuses to touch an existing Sylvia GM registration"
        )
    installer, installer_record, manifest = _manifest_msi(release_directory)
    product = manifest.get("product")
    provenance = manifest.get("provenance")
    if not isinstance(product, dict) or not isinstance(provenance, dict):
        raise ReleaseArtifactError("Release manifest identity is invalid")
    msiexec = _msiexec()

    primary_error: BaseException | None = None
    cleanup_error: BaseException | None = None
    registration: UninstallRegistration | None = None
    installed_owner_receipt: dict[str, object] | None = None
    install_attempted = False
    try:
        install_attempted = True
        _run_checked(
            (
                str(msiexec),
                "/i",
                str(installer),
                "/qn",
                "/norestart",
                f"INSTALLDIR={target}",
            ),
            timeout_seconds=timeout_seconds,
        )
        _require_payload(target)
        installed_registrations = _uninstall_registrations()
        if len(installed_registrations) != 1:
            raise ReleaseArtifactError(
                "MSI install did not create one isolated Sylvia GM registration"
            )
        registration = installed_registrations[0]
        if not _registration_matches(registration):
            raise ReleaseArtifactError(
                "MSI registration does not identify the machine install and version"
            )
        with tempfile.TemporaryDirectory(prefix="sylvia-gm-msi-owner-") as workspace:
            installed_owner_receipt = _qualify_installed_owner_backup_recovery(
                owner_executable=target / _REQUIRED_PAYLOAD[1],
                workspace=Path(workspace),
                timeout_seconds=timeout_seconds,
            )
    except BaseException as error:
        primary_error = error
    finally:
        if install_attempted and (target.exists() or _uninstall_registrations()):
            try:
                _run_checked(
                    (
                        str(msiexec),
                        "/x",
                        str(installer),
                        "/qn",
                        "/norestart",
                    ),
                    timeout_seconds=timeout_seconds,
                )
                _wait_until_removed(target, timeout_seconds=timeout_seconds)
            except BaseException as error:
                cleanup_error = error

    if primary_error is not None:
        if cleanup_error is not None:
            raise ReleaseArtifactError(
                "MSI qualification failed and the isolated install did not cleanly uninstall"
            ) from primary_error
        raise primary_error
    if cleanup_error is not None:
        raise cleanup_error
    if registration is None:
        raise ReleaseArtifactError("MSI qualification produced no registration evidence")
    if installed_owner_receipt is None:
        raise ReleaseArtifactError("MSI qualification produced no installed-owner evidence")

    return {
        "application_executable": _REQUIRED_PAYLOAD[0],
        "host": _windows_host(),
        "install_directory_removed": not target.exists(),
        "installer_kind": "msi-installer",
        "installer_name": installer.name,
        "installer_sha256": installer_record["sha256"],
        "installed_owner": installed_owner_receipt,
        "owner_executable": _REQUIRED_PAYLOAD[1],
        "product": product["name"],
        "registration_hive": registration.hive,
        "registration_removed": not _uninstall_registrations(),
        "registration_version": registration.display_version,
        "source_commit": provenance["source_commit"],
        "source_tree": provenance["source_tree"],
        "version": product["version"],
        "windows_installer_version": product["windows_installer_version"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release_directory", type=Path)
    parser.add_argument("--install-directory", type=Path)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    program_files = os.environ.get("PROGRAMFILES")
    if not program_files:
        raise ReleaseArtifactError("Windows Program Files directory is unavailable")
    target = arguments.install_directory or (
        Path(program_files) / f"Sylvia GM Qualification {os.getpid()}"
    )
    receipt = qualify_msi_install_uninstall(
        release_directory=arguments.release_directory,
        install_directory=target,
        timeout_seconds=arguments.timeout_seconds,
    )
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
