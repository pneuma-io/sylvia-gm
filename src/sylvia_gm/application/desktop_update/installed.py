"""Current-user NSIS replacement and retained-installer recovery for one registered copy."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Protocol

from .manifest import Manifest, PayloadFile, UpdateError, Version, bounded_json
from .payload import verify_artifact
from .replacement import _copy, file_matches
from .runtime import system_child_libraries
from .storage import atomic_receipt, checked_file, directory_identity, ensure_directory

# Match the current Tauri product/identifier and its default publisher fallback, not a search.
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Sylvia GM"
PRODUCT_KEY = r"Software\pneuma-io\Sylvia GM"


class InstalledRegistration(Protocol):
    def read(self) -> Mapping[str, str]: ...


class WindowsRegistration:
    """Read only this product's fixed current-user keys; never enumerate other installations."""

    def read(self) -> Mapping[str, str]:
        if sys.platform != "win32":
            raise UpdateError("unsupported_platform", "NSIS updating requires Windows.")
        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, UNINSTALL_KEY, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY
            ) as key:
                result = {}
                for name in (
                    "DisplayName",
                    "DisplayVersion",
                    "Publisher",
                    "InstallLocation",
                    "MainBinaryName",
                    "UninstallString",
                ):
                    value, kind = winreg.QueryValueEx(key, name)
                    if kind != winreg.REG_SZ or not isinstance(value, str) or len(value) > 4096:
                        raise ValueError("Invalid product registration")
                    result[name] = value
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, PRODUCT_KEY, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY
            ) as key:
                value, kind = winreg.QueryValueEx(key, "")
                if kind != winreg.REG_SZ or not isinstance(value, str) or len(value) > 4096:
                    raise ValueError("Invalid product registration")
                result["ProductLocation"] = value
            return result
        except (OSError, ValueError) as error:
            raise UpdateError(
                "installation_unavailable",
                "This copy is not the registered current-user NSIS installation. "
                "Keep it intact and use an approved manual repair.",
            ) from error


def registered_versions(version: Version) -> set[str]:
    # The existing Alpha release recipe maps alpha.N to the Windows-compatible -N version.
    result = {version.text}
    if (
        len(version.prerelease) == 2
        and version.prerelease[0] == "alpha"
        and version.prerelease[1].isdigit()
    ):
        result.add(".".join(str(part) for part in version.core) + "-" + version.prerelease[1])
    return result


def nsis_command_line(artifact: Path, live: Path) -> str:
    """NSIS requires an unquoted absolute /D tail. No shell, /R or uninstall invocation."""
    if (
        not artifact.is_absolute()
        or not live.is_absolute()
        or any(char in str(artifact) + str(live) for char in ('"', "\r", "\n", "\0"))
        or str(live).startswith(("\\\\", "//"))
    ):
        raise UpdateError("unsafe_installation", "The exact installer destination is unsupported.")
    return subprocess.list2cmdline([str(artifact), "/S", "/UPDATE", "/NS"]) + " /D=" + str(live)


def run_nsis(artifact: Path, live: Path) -> int:
    if sys.platform != "win32":
        raise UpdateError("unsupported_platform", "NSIS updating requires Windows.")
    temporary = artifact.parent / "installer-temp"
    ensure_directory(temporary)
    directory_identity(temporary)
    environment = {
        name: os.environ[name]
        for name in ("SystemRoot", "WINDIR", "PATH", "LOCALAPPDATA", "APPDATA")
        if name in os.environ
    }
    environment.update(TEMP=str(temporary), TMP=str(temporary))
    try:
        with system_child_libraries(), subprocess.Popen(
            nsis_command_line(artifact, live),
            executable=str(artifact),
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=artifact.parent,
            env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW,
        ) as process:
            return process.wait()  # No forced termination or artificial installer time limit.
    except OSError as error:
        raise UpdateError(
            "installer_unavailable",
            "Windows could not start the verified installer. "
            "No permission change or elevation was attempted.",
        ) from error


class InstalledReplacement:
    def __init__(
        self,
        live: Path,
        operation: Path,
        manifest: Manifest,
        previous: Manifest,
        registration: InstalledRegistration,
        run: Callable[[Path, Path], int] = run_nsis,
        *,
        artifacts: Path | None = None,
    ) -> None:
        if (
            manifest.distribution != "installed"
            or previous.distribution != "installed"
            or manifest.target != previous.target
            or manifest.version <= previous.version
        ):
            raise UpdateError(
                "wrong_distribution", "The installed update/recovery pair is invalid."
            )
        self.live, self.operation = live, operation
        self.manifest, self.previous = manifest, previous
        self.registration, self.run = registration, run
        self.artifacts = operation if artifacts is None else artifacts
        self._live_identity = directory_identity(live)
        self._operation_identity = directory_identity(operation)
        self._artifact_identity = directory_identity(self.artifacts)
        if not operation.is_relative_to(self.artifacts):
            raise UpdateError("unsafe_staging", "Installer receipts must belong to this download.")
        if live.is_relative_to(operation) or operation.is_relative_to(live):
            raise UpdateError("unsafe_installation", "Installer staging must be outside the app.")
        self._journal = operation / "installed-replacement.json"

    def _check(self, versions: tuple[Version, ...]) -> None:
        if (
            directory_identity(self.live) != self._live_identity
            or directory_identity(self.operation) != self._operation_identity
            or directory_identity(self.artifacts) != self._artifact_identity
            or (self.live / "sylvia-portable.json").exists()
            or (self.live / "sylvia-portable.json").is_symlink()
        ):
            raise UpdateError("wrong_installation", "The registered application location changed.")
        row = self.registration.read()
        if (
            row.get("DisplayName") != "Sylvia GM"
            or row.get("Publisher") != "pneuma-io"
            or row.get("MainBinaryName") != "sylvia-gm.exe"
            or row.get("DisplayVersion")
            not in set().union(*(registered_versions(version) for version in versions))
        ):
            raise UpdateError("wrong_installation", "The registered NSIS copy is inconsistent.")
        uninstall = row.get("UninstallString", "")
        if (
            not uninstall.startswith('"')
            or not uninstall.endswith('"')
            or '"' in uninstall[1:-1]
            or Path(uninstall[1:-1]) != self.live / "uninstall.exe"
        ):
            raise UpdateError("wrong_installation", "The NSIS registration is inconsistent.")
        for field in ("InstallLocation", "ProductLocation"):
            location = row.get(field, "").strip('"')
            if not location or Path(location) != self.live:
                raise UpdateError("wrong_installation", "Updating another copy is not permitted.")
        verify_artifact(self.artifacts / "artifact.verified", self.manifest)
        verify_artifact(self.artifacts / "previous-installer/artifact.verified", self.previous)

    def _save(self, phase: str) -> None:
        atomic_receipt(
            self._journal,
            {
                "format_version": 1,
                "phase": phase,
                "version": self.manifest.version.text,
                "previous_version": self.previous.version.text,
            },
        )

    def _phase(self) -> str:
        row = bounded_json(checked_file(self._journal, maximum=4096))
        if (
            not isinstance(row, dict)
            or set(row) != {"format_version", "phase", "version", "previous_version"}
            or type(row["format_version"]) is not int
            or row["format_version"] != 1
            or row["version"] != self.manifest.version.text
            or row["previous_version"] != self.previous.version.text
            or row["phase"]
            not in {
                "prepared",
                "applying",
                "awaiting_restart",
                "rolling_back",
                "rolled_back",
                "confirmed",
            }
        ):
            raise UpdateError("invalid_journal", "Installed recovery needs reconciliation.")
        return str(row["phase"])

    def _matches(self, manifest: Manifest) -> bool:
        return all(file_matches(self.live / row.path, row) for row in manifest.files)

    def _mixed_files(self) -> None:
        old = {row.path: row for row in self.previous.files}
        new = {row.path: row for row in self.manifest.files}
        for name in old.keys() | new.keys():
            target = self.live / name
            if any(
                file_matches(target, row)
                for row in (old.get(name), new.get(name))
                if row is not None
            ):
                continue
            if not target.exists() and not target.is_symlink():
                continue  # A partial NSIS copy can leave an expected destination absent.
            raise UpdateError("application_changed", "A manual application repair is preserved.")

    def prepare(self) -> None:
        self._check((self.previous.version,))
        if self._journal.exists() or self._journal.is_symlink():
            raise UpdateError("staging_exists", "Existing installed preparation must be retained.")
        if not self._matches(self.previous):
            raise UpdateError("application_changed", "Current app files do not match recovery.")
        self._mixed_files()
        if (
            shutil.disk_usage(self.live).free
            < sum(row.size for row in self.manifest.files) + 16 * 1024**2
        ):
            raise UpdateError("disk_full", "There is not enough space to apply this update safely.")
        self._save("prepared")

    def _retire(
        self, source: Manifest, keep: Manifest, directory: str, processes_exited: Callable[[], bool]
    ) -> None:
        keep_names = {row.path for row in keep.files}
        for row in source.files:
            if row.path in keep_names:
                continue
            target = self.live / row.path
            if not target.exists() and not target.is_symlink():
                continue
            if not processes_exited() or not file_matches(target, row):
                raise UpdateError("application_changed", "An obsolete application file changed.")
            retained = self.operation / directory / row.path
            ensure_directory(retained.parent)
            directory_identity(retained.parent)
            if retained.exists() or retained.is_symlink():
                if not file_matches(retained, row):
                    raise UpdateError("staging_exists", "An existing retired file is preserved.")
            else:
                # Installed application and per-user recovery storage may be on
                # different volumes. Publish a verified copy before unlinking the
                # source; a crash between these steps safely retains both copies.
                if shutil.disk_usage(self.operation).free < row.size + 16 * 1024**2:
                    raise UpdateError("disk_full", "There is not enough recovery storage.")
                temporary = retained.parent / f".retiring-{uuid.uuid4().hex}.tmp"
                _copy(target, temporary, row)
                if retained.exists() or retained.is_symlink():
                    raise UpdateError("staging_exists", "An existing retired file is preserved.")
                os.replace(temporary, retained)
            if not processes_exited():
                raise UpdateError(
                    "application_busy", "Close owned processes before retiring files."
                )
            if not file_matches(retained, row) or not file_matches(target, row):
                raise UpdateError("application_changed", "An obsolete application file changed.")
            target.unlink()

    def apply(self, processes_exited: Callable[[], bool]) -> None:
        self._check((self.previous.version, self.manifest.version))
        if self._phase() not in {"prepared", "applying", "awaiting_restart"}:
            raise UpdateError("invalid_phase", "This installer cannot apply in its current state.")
        if not processes_exited():
            raise UpdateError("application_busy", "Close all owned processes before updating.")
        self._mixed_files()
        self._save("applying")
        needed = not self._matches(self.manifest) or self.registration.read().get(
            "DisplayVersion"
        ) not in registered_versions(self.manifest.version)
        if needed and self.run(self.artifacts / "artifact.verified", self.live) != 0:
            raise UpdateError(
                "installer_failed",
                "The installer did not complete. "
                "Recovery files are retained; no restart was attempted.",
            )
        self._check((self.manifest.version,))
        if not self._matches(self.manifest):
            raise UpdateError(
                "installation_incomplete", "Installed files failed final verification."
            )
        self._retire(self.previous, self.manifest, "retired", processes_exited)
        self._save("awaiting_restart")

    def rollback(
        self, processes_exited: Callable[[], bool], compatibility: Callable[[], None]
    ) -> None:
        self._check((self.previous.version, self.manifest.version))
        self._phase()
        if not processes_exited():
            raise UpdateError("application_busy", "Close all owned processes before recovery.")
        compatibility()  # A deliberate recovery path, never a normal downgrade offer.
        self._mixed_files()
        self._save("rolling_back")
        needed = not self._matches(self.previous) or self.registration.read().get(
            "DisplayVersion"
        ) not in registered_versions(self.previous.version)
        if (
            needed
            and self.run(self.artifacts / "previous-installer/artifact.verified", self.live) != 0
        ):
            raise UpdateError(
                "recovery_failed", "The retained installer could not repair this copy."
            )
        self._check((self.previous.version,))
        if not self._matches(self.previous):
            raise UpdateError("recovery_failed", "Recovered application files failed verification.")
        self._retire(self.manifest, self.previous, "failed-new", processes_exited)
        self._save("rolled_back")

    def confirm(self) -> None:
        self._check((self.manifest.version,))
        if self._phase() not in {"awaiting_restart", "confirmed"}:
            raise UpdateError(
                "invalid_phase", "This installer has not reached restart confirmation."
            )
        self._save("confirmed")

    def helper_payload(self) -> tuple[Path, PayloadFile]:
        row = next((row for row in self.previous.files if row.path == "sylvia-gm-owner.exe"), None)
        if row is None:
            raise UpdateError(
                "invalid_installation", "The current helper is missing from recovery."
            )
        return self.live / row.path, row

    def verify_rollback(self) -> None:
        """Require the exact previous registered copy before acknowledging recovery."""
        self._check((self.previous.version,))
        if self._phase() != "rolled_back" or not self._matches(self.previous):
            raise UpdateError("recovery_incomplete", "Installed recovery has not finished.")
        old = {row.path for row in self.previous.files}
        for row in self.manifest.files:
            path = self.live / row.path
            if row.path not in old and (path.exists() or path.is_symlink()):
                raise UpdateError("application_changed", "A recovered application path changed.")
