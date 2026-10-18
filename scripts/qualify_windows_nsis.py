"""Qualify one manifest-bound NSIS install/uninstall in an isolated directory."""

from __future__ import annotations

import argparse
import json
import os
import platform
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO, cast

from sylvia_gm.covered_source_delivery import covered_source_required
from sylvia_gm.infrastructure.launch_handshake import LaunchHandshake
from sylvia_gm.release_artifact import (
    DEPENDENCY_NOTICES_PATH,
    WINDOWS_INSTALLER_VERSION,
    ReleaseArtifactError,
    assemble_qualified_release_layout,
    read_dependency_notices,
    read_notice_companions,
    verify_release_layout,
)

_PRODUCT_NAME = "Sylvia GM"
_REQUIRED_PAYLOAD = (
    "sylvia-gm.exe",
    "sylvia-gm-owner.exe",
    "uninstall.exe",
    DEPENDENCY_NOTICES_PATH,
)
_UNINSTALL_ROOT = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"


@dataclass(frozen=True, slots=True)
class UninstallRegistration:
    """Path-private Windows uninstall registration fields used by qualification."""

    hive: str
    key: str
    display_name: str
    display_version: str
    install_location: str
    uninstall_string: str


class _OwnerProtocol:
    """Minimal authenticated client for one installed owner qualification process."""

    def __init__(
        self,
        *,
        executable: Path,
        database: Path,
        owner_id: str,
        timeout_seconds: float,
    ) -> None:
        database.parent.mkdir(parents=True, exist_ok=True)
        try:
            process = subprocess.Popen(
                (str(executable),),
                cwd=database.parent,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                # The client reads stdout only; an unread stderr pipe can block startup.
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except OSError as error:
            raise ReleaseArtifactError("Installed owner could not be launched") from error
        if process.stdin is None or process.stdout is None:
            process.kill()
            raise ReleaseArtifactError("Installed owner did not expose its local protocol")
        self._process = process
        self._input = cast(TextIO, process.stdin)
        self._output = cast(TextIO, process.stdout)
        self._timeout_seconds = timeout_seconds
        self._closed = False

        handshake = LaunchHandshake()
        credential = handshake.issue()
        try:
            self._write(
                {
                    "type": "launch",
                    "secret_hex": credential.secret_hex,
                    "database_path": str(database),
                    "owner_id": owner_id,
                }
            )
            ready = self._read()
            if ready.get("ok") is not True or ready.get("type") != "ready":
                raise ReleaseArtifactError("Installed owner did not become ready")
            response = ready.get("response_hex")
            if not isinstance(response, str):
                raise ReleaseArtifactError("Installed owner launch response is invalid")
            handshake.verify(response)
        except BaseException:
            self._terminate()
            raise

    def _write(self, payload: dict[str, object]) -> None:
        if self._process.poll() is not None:
            raise ReleaseArtifactError("Installed owner exited before receiving a command")
        try:
            self._input.write(json.dumps(payload, separators=(",", ":")) + "\n")
            self._input.flush()
        except OSError as error:
            raise ReleaseArtifactError("Installed owner command transport failed") from error

    def _read(self) -> dict[str, object]:
        result: queue.Queue[str | BaseException] = queue.Queue(maxsize=1)

        def read_line() -> None:
            try:
                result.put(self._output.readline())
            except BaseException as error:
                result.put(error)

        threading.Thread(target=read_line, daemon=True).start()
        try:
            item = result.get(timeout=self._timeout_seconds)
        except queue.Empty as error:
            raise ReleaseArtifactError("Installed owner response timed out") from error
        if isinstance(item, BaseException):
            raise ReleaseArtifactError("Installed owner response transport failed") from item
        if not item:
            raise ReleaseArtifactError("Installed owner exited before replying")
        try:
            decoded = json.loads(item)
        except json.JSONDecodeError as error:
            raise ReleaseArtifactError("Installed owner response is not valid JSON") from error
        if not isinstance(decoded, dict):
            raise ReleaseArtifactError("Installed owner response is not an object")
        return decoded

    def request(
        self,
        request_id: str,
        method: str,
        params: dict[str, object] | None = None,
    ) -> object:
        self._write(
            {
                "id": request_id,
                "method": method,
                "params": {} if params is None else params,
            }
        )
        reply = self._read()
        if reply.get("id") != request_id:
            raise ReleaseArtifactError("Installed owner response identity does not match")
        if reply.get("ok") is not True:
            error = reply.get("error")
            code = error.get("code") if isinstance(error, dict) else None
            safe_code = code if isinstance(code, str) and code else "owner_rejected"
            raise ReleaseArtifactError(f"Installed owner rejected {method}: {safe_code}")
        return reply.get("result")

    def _terminate(self) -> None:
        if self._process.poll() is None:
            self._process.kill()
        with suppress(subprocess.TimeoutExpired):
            self._process.wait(timeout=self._timeout_seconds)
        self._closed = True

    def shutdown(self) -> None:
        if self._closed:
            return
        try:
            self.request("shutdown", "shutdown")
            self._process.wait(timeout=self._timeout_seconds)
        except BaseException:
            self._terminate()
            raise
        self._closed = True
        if self._process.returncode != 0:
            raise ReleaseArtifactError("Installed owner did not shut down cleanly")

    def __enter__(self) -> _OwnerProtocol:
        return self

    def __exit__(self, error_type: object, _error: object, _traceback: object) -> None:
        if error_type is None:
            self.shutdown()
            return
        self._terminate()


def _run_checked(command: tuple[str, ...], *, timeout_seconds: float) -> None:
    try:
        completed = subprocess.run(
            command,
            check=False,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ReleaseArtifactError(
            f"Windows qualification command could not complete: {Path(command[0]).name}"
        ) from error
    if completed.returncode != 0:
        raise ReleaseArtifactError(f"Windows qualification command failed: {Path(command[0]).name}")


def _registry_text(key: Any, name: str) -> str:
    try:
        import winreg

        value, _kind = winreg.QueryValueEx(key, name)
    except OSError:
        return ""
    return value if isinstance(value, str) else ""


def _uninstall_registrations() -> tuple[UninstallRegistration, ...]:
    if os.name != "nt":
        raise ReleaseArtifactError("NSIS qualification is available only on Windows")
    import winreg

    registrations: dict[tuple[str, str], UninstallRegistration] = {}
    views = (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY)
    for hive_name, hive in (
        ("HKCU", winreg.HKEY_CURRENT_USER),
        ("HKLM", winreg.HKEY_LOCAL_MACHINE),
    ):
        for view in views:
            try:
                root = winreg.OpenKey(hive, _UNINSTALL_ROOT, 0, winreg.KEY_READ | view)
            except OSError:
                continue
            with root:
                index = 0
                while True:
                    try:
                        child_name = winreg.EnumKey(root, index)
                    except OSError:
                        break
                    index += 1
                    try:
                        child = winreg.OpenKey(root, child_name, 0, winreg.KEY_READ | view)
                    except OSError:
                        continue
                    with child:
                        display_name = _registry_text(child, "DisplayName")
                        if display_name.casefold() != _PRODUCT_NAME.casefold():
                            continue
                        key_path = f"{_UNINSTALL_ROOT}\\{child_name}"
                        registration = UninstallRegistration(
                            hive=hive_name,
                            key=key_path,
                            display_name=display_name,
                            display_version=_registry_text(child, "DisplayVersion"),
                            install_location=_registry_text(child, "InstallLocation"),
                            uninstall_string=_registry_text(child, "UninstallString"),
                        )
                        registrations[(hive_name, key_path.casefold())] = registration
    return tuple(
        registrations[key] for key in sorted(registrations, key=lambda item: (item[0], item[1]))
    )


def _manifest_nsis(
    release_directory: Path,
) -> tuple[Path, dict[str, object], dict[str, object]]:
    manifest = verify_release_layout(release_directory)
    records = manifest["artifacts"]
    if not isinstance(records, list):
        raise ReleaseArtifactError("Release manifest artifact list is invalid")
    matches = [record for record in records if record.get("kind") == "nsis-installer"]
    if len(matches) != 1:
        raise ReleaseArtifactError("Release manifest has no unique NSIS installer")
    record = matches[0]
    name = record.get("name")
    if not isinstance(name, str):
        raise ReleaseArtifactError("Release NSIS installer name is invalid")
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
    if (
        not install_directory.is_dir()
        or install_directory.is_symlink()
        or install_directory.resolve(strict=True) != install_directory
    ):
        raise ReleaseArtifactError("NSIS installer did not create the isolated directory")
    for name in _REQUIRED_PAYLOAD:
        path = install_directory / name
        if not path.is_file() or path.is_symlink():
            raise ReleaseArtifactError(f"NSIS installer omitted required payload: {name}")


def _require_installed_dependency_notices(install_directory: Path, expected: bytes) -> None:
    """Read the persistent installed resource, independent of owner onefile extraction."""

    installed = read_dependency_notices(install_directory / DEPENDENCY_NOTICES_PATH)
    if installed != expected:
        raise ReleaseArtifactError("Installed dependency notices do not match the pinned release")


def _registration_matches(
    registration: UninstallRegistration,
    install_directory: Path,
) -> bool:
    if (
        registration.display_name.casefold() != _PRODUCT_NAME.casefold()
        or registration.display_version != WINDOWS_INSTALLER_VERSION
    ):
        return False
    command = registration.uninstall_string.strip()
    if command.startswith('"'):
        executable, quote, arguments = command[1:].partition('"')
        if not quote or (arguments and not arguments[0].isspace()):
            return False
    else:
        executable = command.split(maxsplit=1)[0] if command else ""
    # The generated NSIS registry writer wraps InstallLocation in one pair of quotes.
    install_location = registration.install_location
    if install_location.startswith('"') and install_location.endswith('"'):
        install_location = install_location[1:-1]
    if (
        '"' in install_location
        or not Path(install_location).is_absolute()
        or not Path(executable).is_absolute()
    ):
        return False
    expected_directory = os.path.normcase(os.path.normpath(str(install_directory)))
    registered_directory = os.path.normcase(os.path.normpath(install_location))
    uninstaller = os.path.normcase(os.path.normpath(str(install_directory / "uninstall.exe")))
    return (
        registered_directory == expected_directory
        and os.path.normcase(os.path.normpath(executable)) == uninstaller
    )


def _require_isolated_registration(install_directory: Path) -> UninstallRegistration:
    """Require one registration whose directory and executable both identify this target."""

    registrations = _uninstall_registrations()
    if len(registrations) != 1:
        raise ReleaseArtifactError(
            "NSIS install did not create one isolated Sylvia GM registration"
        )
    registration = registrations[0]
    if not _registration_matches(registration, install_directory):
        raise ReleaseArtifactError(
            "NSIS registration does not identify the isolated install and version"
        )
    return registration


def _require_owned_uninstaller(install_directory: Path) -> Path:
    """Reject a missing or redirected target before invoking its own uninstaller."""

    uninstaller = install_directory / "uninstall.exe"
    if (
        not install_directory.is_dir()
        or install_directory.is_symlink()
        or install_directory.resolve(strict=True) != install_directory
        or not uninstaller.is_file()
        or uninstaller.is_symlink()
    ):
        raise ReleaseArtifactError("NSIS isolated uninstaller is missing or redirected")
    return uninstaller


def _wait_until_removed(install_directory: Path, *, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        if not install_directory.exists() and not _uninstall_registrations():
            return
        if time.monotonic() >= deadline:
            raise ReleaseArtifactError(
                "NSIS uninstall left its install directory or Windows registration behind"
            )
        time.sleep(0.1)


def _windows_host() -> dict[str, object]:
    if os.name != "nt":
        raise ReleaseArtifactError("NSIS qualification is available only on Windows")
    version = sys.getwindowsversion()
    return {
        "architecture": platform.machine(),
        "build": version.build,
        "major": version.major,
        "minor": version.minor,
        "platform_version": platform.version(),
    }


def _owner_result_mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ReleaseArtifactError(f"Installed owner {label} result is invalid")
    return value


def _owner_result_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReleaseArtifactError(f"Installed owner {label} is invalid")
    return value


def _owner_result_int(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ReleaseArtifactError(f"Installed owner {label} is invalid")
    return value


def _prepared_opponent_hp(snapshot: dict[str, object]) -> int:
    """Read the selected current-Plan play-state witness from an owner snapshot."""

    state = _owner_result_mapping(snapshot.get("state"), "current-Plan campaign state")
    entities = _owner_result_mapping(
        state.get("sylvia.srd-lite.bg3.entities"),
        "current-Plan entity state",
    )
    opponent = _owner_result_mapping(
        entities.get("installed-qualification-opponent"),
        "prepared opponent state",
    )
    return _owner_result_int(opponent.get("hp"), "prepared opponent hit points")


def _player_character_id(snapshot: dict[str, object]) -> str:
    """Read the selected Player Character identity from an owner snapshot."""

    participants = snapshot.get("participants")
    if not isinstance(participants, list):
        raise ReleaseArtifactError("Installed owner current-Plan participants are invalid")
    for value in participants:
        if not isinstance(value, dict) or value.get("kind") != "player_character":
            continue
        return _owner_result_text(
            value.get("participant_id"), "current-Plan Player Character identity"
        )
    raise ReleaseArtifactError("Installed owner current-Plan Player Character is missing")


def _combat_is_active(snapshot: dict[str, object]) -> bool:
    """Read the selected current-Plan combat persistence witness from an owner snapshot."""

    state = _owner_result_mapping(snapshot.get("state"), "current-Plan campaign state")
    combat = _owner_result_mapping(
        state.get("sylvia.srd-lite.bg3.combat"), "current-Plan combat state"
    )
    return combat.get("encounter_active") is True


def _qualification_combat_scene(player_id: str) -> dict[str, object]:
    """Declare the synthetic encounter's physical arena through the supported scene input."""
    return {
        "format": 1,
        "map": {
            "id": "installed-qualification",
            "revision": 0,
            "bounds": {"min": [-30, -30, -10], "max": [30, 30, 20]},
            "grid": {"cell_size": 5, "unit": "ft", "origin": [-30, -30]},
            "layers": [{"id": "world", "min_elevation": -10, "max_elevation": 20}],
        },
        "entities": [
            {
                "id": actor,
                "map_id": "installed-qualification",
                "position": position,
                "footprint": {"kind": "circle", "radius": 0.5},
                "height": 6,
                "facing": [1, 0],
                "active": True,
                "movement": "blocking",
                "visibility": "opaque",
                "metadata": {
                    "participant_ref": actor,
                    "creature_type": "humanoid",
                    "plane": "material",
                    "lighting": "bright",
                    "player": {"label": label, "kind": "unit"},
                },
            }
            for actor, position, label in [
                (player_id, [0, 0, 0], "Tavin"),
                ("installed-qualification-opponent", [5, 0, 0], "Synthetic opponent"),
            ]
        ],
        "geometry": [],
        "zones": [],
        "terrain": [],
    }


def _qualify_installed_owner_backup_recovery(
    *,
    owner_executable: Path,
    workspace: Path,
    timeout_seconds: float,
) -> dict[str, object]:
    """Prove backup/reopen and portable cross-root recovery through installed bytes."""

    owner_a_root = workspace / "installed-owner-a"
    owner_b_root = workspace / "installed-owner-b"
    portable_archive = workspace / "selected-recovery.sylvia-gm-campaign"
    for path in (owner_a_root, owner_b_root, portable_archive):
        if path.exists() or path.is_symlink():
            raise ReleaseArtifactError("Installed owner qualification workspace is not fresh")

    owner_a_database = owner_a_root / "authority.sqlite3"
    with _OwnerProtocol(
        executable=owner_executable,
        database=owner_a_database,
        owner_id="owner-installed-qualification-a",
        timeout_seconds=timeout_seconds,
    ) as owner_a:
        owner_a.request(
            "configure-offline-profile",
            "save_provider_settings",
            {
                "provider_id": "custom",
                "preset_id": "custom",
                "display_name": "Offline installed qualification",
                "base_url": "http://127.0.0.1:9/v1",
                "model_id": "synthetic-installed-model",
                "model_name": "Synthetic installed model",
                "context_window": 262144,
                "max_output_tokens": 8192,
                "transport": "chat_completions",
                "supports_streaming": True,
                "supports_tools": True,
            },
        )
        models = owner_a.request("offline-model-choices", "model_choices")
        if not isinstance(models, list):
            raise ReleaseArtifactError("Installed owner model choices are invalid")
        model = next(
            (
                row
                for row in models
                if isinstance(row, dict)
                and "synthetic-installed-model" in str(row.get("model_id", ""))
            ),
            None,
        )
        if model is None:
            raise ReleaseArtifactError("Installed owner did not retain the synthetic model")
        provider_id = _owner_result_text(model.get("provider_id"), "effective provider identity")
        model_id = _owner_result_text(model.get("model_id"), "effective model identity")
        sources = {
            "ruleset_selection": "sylvia.srd-lite.bg3",
            "setting_id": "pneuma.severed-lands",
            "plan_id": "a-debt-in-iron",
        }
        options = _owner_result_mapping(
            owner_a.request("current-party-options", "campaign_party_options", sources),
            "current Plan party options",
        )
        archetypes = options.get("player_archetypes")
        if not isinstance(archetypes, list):
            raise ReleaseArtifactError("Installed owner party options are invalid")
        player = next(
            (
                row
                for row in archetypes
                if isinstance(row, dict) and row.get("campaign_ready") is True
            ),
            None,
        )
        if player is None:
            raise ReleaseArtifactError("Current Plan has no campaign-ready player")
        player_archetype = _owner_result_text(player.get("id"), "player archetype")
        draft = _owner_result_mapping(
            owner_a.request(
                "draft-installed",
                "create_guided_campaign_draft",
                {
                    "operation_id": "draft-installed",
                    "display_name": "Installed Debt in Iron Qualification",
                    "provider_id": provider_id,
                    "model_id": model_id,
                },
            ),
            "current campaign draft",
        )
        campaign_id = _owner_result_text(draft.get("campaign_id"), "campaign identity")
        builder_session = _owner_result_text(
            draft.get("builder_session_id"), "Builder session identity"
        )
        answers = (
            ("ruleset", {"candidate_id": sources["ruleset_selection"]}),
            ("setting", {"setting_id": sources["setting_id"]}),
            ("plan", {"plan_id": sources["plan_id"]}),
            ("name", {"display_name": "Installed Debt in Iron Qualification"}),
            ("participants", {"archetype_id": player_archetype, "companion_ids": []}),
            ("boundaries_preferences", {"boundaries": "", "preferences": ""}),
            ("specific_requests", {"no_additional_requests": True, "requests": ""}),
        )
        for revision, (step, answer) in enumerate(answers):
            owner_a.request(
                "answer-installed-" + step,
                "advance_guided_campaign_draft",
                {
                    "operation_id": "answer-installed-" + step,
                    "campaign_id": campaign_id,
                    "builder_session_id": builder_session,
                    "expected_construction_revision": revision,
                    "current_step": step,
                    "visible_answer": step,
                    "answer": answer,
                },
            )
        owner_a.request(
            "start-installed",
            "start_guided_campaign",
            {
                "operation_id": "start-installed",
                "campaign_id": campaign_id,
                "builder_session_id": builder_session,
                "display_name": "Installed Debt in Iron Qualification",
                "candidate_id": sources["ruleset_selection"],
                "setting_id": sources["setting_id"],
                "plan_id": sources["plan_id"],
                "player_character_id": player_archetype,
                "companion_ids": [],
                "premise": "",
                "construction_intent": {
                    "boundaries": "",
                    "preferences": "",
                    "specific_requests": "",
                    "contributions": {},
                },
            },
        )
        finalized = _owner_result_mapping(
            owner_a.request(
                "finalize-installed",
                "finalize_campaign_construction",
                {
                    "campaign_id": campaign_id,
                    "dm_provider_id": provider_id,
                    "dm_model_id": model_id,
                    "model_guidance_override": True,
                },
            ),
            "campaign finalization",
        )
        handoff = _owner_result_mapping(finalized.get("handoff"), "campaign handoff")
        if handoff.get("campaign_id") != campaign_id:
            raise ReleaseArtifactError("Installed owner handoff changed campaign identity")
        dm_session_id = _owner_result_text(handoff.get("dm_session_id"), "DM session identity")
        dm_turn_id = _owner_result_text(finalized.get("dm_turn_id"), "DM TURN identity")
        before_action = _owner_result_mapping(
            owner_a.request(
                "snapshot-before-play",
                "snapshot",
                {"campaign_id": campaign_id},
            ),
            "pre-play snapshot",
        )
        player_id = _player_character_id(before_action)
        prepared = _owner_result_mapping(
            owner_a.request(
                "prepare-installed-opponent",
                "execute_action",
                {
                    "campaign_id": campaign_id,
                    "action_id": "prepare-installed-opponent",
                    "idempotency_key": "prepare-installed-opponent",
                    "writer": "dm",
                    "expected_revision": _owner_result_int(
                        before_action.get("revision"), "pre-play revision"
                    ),
                    "turn_id": dm_turn_id,
                    "session_id": dm_session_id,
                    "package_id": "sylvia.srd-lite.bg3",
                    "tool_name": "prepare_srd_creature",
                    "parameters": {
                        "creature_id": "installed-qualification-opponent",
                        "name": "Synthetic qualification opponent",
                        "source": (
                            "Explicit synthetic GM-adjudicated test creature; "
                            "no authored Plan encounter is replaced."
                        ),
                        "armor_class": 12,
                        "hit_points": 9,
                        "speed": 30,
                        "size_rank": 2,
                        "initiative_modifier": 0,
                    },
                },
            ),
            "prepared synthetic opponent",
        )
        if prepared.get("status") != "committed":
            raise ReleaseArtifactError("Installed owner opponent preparation did not commit")
        before_action = _owner_result_mapping(
            owner_a.request("snapshot-prepared", "snapshot", {"campaign_id": campaign_id}),
            "prepared snapshot",
        )
        if _prepared_opponent_hp(before_action) != 9:
            raise ReleaseArtifactError("Installed owner prepared opponent state does not match")
        action = _owner_result_mapping(
            owner_a.request(
                "execute-current-combat-start",
                "execute_action",
                {
                    "campaign_id": campaign_id,
                    "action_id": "action-installed-current-combat-start",
                    "idempotency_key": "idempotency-installed-current-combat-start",
                    "writer": "dm",
                    "expected_revision": _owner_result_int(
                        before_action.get("revision"),
                        "pre-play revision",
                    ),
                    "turn_id": dm_turn_id,
                    "package_id": "sylvia.srd-lite.bg3",
                    "tool_name": "start_combat_encounter_entities",
                    "parameters": {
                        "first_actor": player_id,
                        "second_actor": "installed-qualification-opponent",
                        "first_surprised": False,
                        "second_surprised": False,
                        "tie_winner": "first",
                        "combat_scenes": [_qualification_combat_scene(player_id)],
                    },
                    "session_id": dm_session_id,
                },
            ),
            "current-Plan combat-start action",
        )
        if action.get("status") != "committed":
            raise ReleaseArtifactError("Installed current-Plan mechanics did not commit")
        played = _owner_result_mapping(
            owner_a.request(
                "snapshot-after-play",
                "snapshot",
                {"campaign_id": campaign_id},
            ),
            "played current-Plan snapshot",
        )
        if _prepared_opponent_hp(played) != 9 or not _combat_is_active(played):
            raise ReleaseArtifactError("Installed current-Plan opening encounter did not persist")
        owner_a.request(
            "complete-current-turn",
            "complete_turn",
            {"campaign_id": campaign_id, "turn_id": dm_turn_id},
        )
        played = _owner_result_mapping(
            owner_a.request("snapshot-completed", "snapshot", {"campaign_id": campaign_id}),
            "completed snapshot",
        )
        owner_a.request(
            "backup-installed",
            "backup_database",
            {"backup_id": "installed-backup"},
        )
        export_payload = _owner_result_mapping(
            owner_a.request(
                "export-installed",
                "export_campaign_file",
                {
                    "export_id": "installed-export",
                    "operation_id": "export-installed",
                    "campaign_id": campaign_id,
                },
            ),
            "campaign export",
        )

    backup = owner_a_root / "backups" / "installed-backup.sqlite3"
    if not backup.is_file() or backup.is_symlink():
        raise ReleaseArtifactError("Installed owner backup artifact is missing")
    export_result = _owner_result_mapping(export_payload.get("export"), "export receipt")
    internal_archive = Path(
        _owner_result_text(export_payload.get("artifact_path"), "export artifact path")
    ).resolve(strict=True)
    expected_exports = (owner_a_root / "exports").resolve(strict=True)
    if (
        internal_archive.parent != expected_exports
        or not internal_archive.is_file()
        or internal_archive.is_symlink()
    ):
        raise ReleaseArtifactError("Installed owner export artifact escaped its storage root")
    shutil.copyfile(internal_archive, portable_archive)

    with _OwnerProtocol(
        executable=owner_executable,
        database=backup,
        owner_id="owner-installed-qualification-backup",
        timeout_seconds=timeout_seconds,
    ) as backup_owner:
        backup_index = backup_owner.request("index-backup", "campaign_index")
        if not isinstance(backup_index, list) or len(backup_index) != 1:
            raise ReleaseArtifactError("Installed owner backup did not reopen exactly")
        backup_entry = _owner_result_mapping(backup_index[0], "backup campaign index")
        if backup_entry.get("campaign_id") != campaign_id:
            raise ReleaseArtifactError("Installed owner backup changed campaign identity")
        backup_snapshot = _owner_result_mapping(
            backup_owner.request(
                "snapshot-backup",
                "snapshot",
                {"campaign_id": campaign_id},
            ),
            "backup current-Plan snapshot",
        )
        if backup_snapshot != played:
            raise ReleaseArtifactError("Installed owner backup lost played current-Plan state")

    owner_b_database = owner_b_root / "authority.sqlite3"
    with _OwnerProtocol(
        executable=owner_executable,
        database=owner_b_database,
        owner_id="owner-installed-qualification-b",
        timeout_seconds=timeout_seconds,
    ) as owner_b:
        recovered = _owner_result_mapping(
            owner_b.request(
                "recover-installed",
                "recover_campaign_file",
                {
                    "recovery_id": "recover-installed",
                    "source_path": str(portable_archive),
                },
            ),
            "campaign recovery",
        )
        if recovered.get("campaign_id") != campaign_id:
            raise ReleaseArtifactError("Installed owner recovery changed campaign identity")
        recovered_index = owner_b.request("index-recovered", "campaign_index")
        if not isinstance(recovered_index, list) or len(recovered_index) != 1:
            raise ReleaseArtifactError("Installed owner recovery index is invalid")
        recovered_entry = _owner_result_mapping(
            recovered_index[0],
            "recovered campaign index",
        )
        if recovered_entry.get("campaign_id") != campaign_id:
            raise ReleaseArtifactError("Installed owner recovery did not reopen the campaign")
        recovered_snapshot = _owner_result_mapping(
            owner_b.request(
                "snapshot-recovered",
                "snapshot",
                {"campaign_id": campaign_id},
            ),
            "recovered current-Plan snapshot",
        )
        if recovered_snapshot != played:
            raise ReleaseArtifactError("Installed owner recovery lost played current-Plan state")

    captured_revision = _owner_result_int(
        recovered.get("captured_revision"),
        "recovered revision",
    )
    if captured_revision != _owner_result_int(
        export_result.get("captured_revision"),
        "exported revision",
    ):
        raise ReleaseArtifactError("Installed owner recovery changed the captured revision")
    archive_sha256 = _owner_result_text(
        recovered.get("archive_sha256"),
        "recovered archive hash",
    )
    if archive_sha256 != _owner_result_text(
        export_result.get("archive_sha256"),
        "export archive hash",
    ):
        raise ReleaseArtifactError("Installed owner recovery changed the archive identity")

    portable_archive.unlink()
    for root in (owner_a_root, owner_b_root):
        resolved = root.resolve(strict=True)
        if resolved.parent != workspace.resolve(strict=True):
            raise ReleaseArtifactError("Refusing to clean an unexpected owner probe directory")
        shutil.rmtree(resolved)
    return {
        "archive_sha256": archive_sha256,
        "backup_reopened": True,
        "captured_revision": captured_revision,
        "portable_recovery": True,
        "combat_played": True,
        "plan_id": "a-debt-in-iron",
        "provider_inference": False,
    }


def qualify_nsis_install_uninstall(
    *,
    release_directory: Path,
    install_directory: Path,
    timeout_seconds: float = 120.0,
) -> dict[str, object]:
    """Install and remove one exact NSIS artifact without touching an existing install."""

    if timeout_seconds <= 0:
        raise ReleaseArtifactError("NSIS qualification timeout must be positive")
    target = _require_new_install_directory(install_directory)
    host = _windows_host()
    host_build = host.get("build")
    if not isinstance(host_build, int) or isinstance(host_build, bool) or host_build < 22_000:
        raise ReleaseArtifactError("NSIS Alpha qualification requires Windows 11")
    if _uninstall_registrations():
        raise ReleaseArtifactError(
            "NSIS qualification refuses to touch an existing Sylvia GM registration"
        )
    installer, installer_record, manifest = _manifest_nsis(release_directory)
    notice_record = cast(dict[str, str], manifest["dependency_notices"])
    expected_notices = read_dependency_notices(
        release_directory / DEPENDENCY_NOTICES_PATH,
        expected_sha256=notice_record["sha256"],
    )
    expected_companions = (
        read_notice_companions(
            release_directory / "licenses",
            include_covered_source=covered_source_required(expected_notices),
        )
        if "companions" in notice_record
        else {}
    )
    product = manifest.get("product")
    provenance = manifest.get("provenance")
    if not isinstance(product, dict) or not isinstance(provenance, dict):
        raise ReleaseArtifactError("Release manifest identity is invalid")

    primary_error: BaseException | None = None
    cleanup_error: BaseException | None = None
    registration: UninstallRegistration | None = None
    installed_owner_receipt: dict[str, object] | None = None
    try:
        _run_checked(
            (str(installer), "/S", f"/D={target}"),
            timeout_seconds=timeout_seconds,
        )
        registration = _require_isolated_registration(target)
        _require_payload(target)
        _require_installed_dependency_notices(target, expected_notices)
        if (
            expected_companions
            and read_notice_companions(
                target / "licenses",
                include_covered_source=covered_source_required(expected_notices),
            )
            != expected_companions
        ):
            raise ReleaseArtifactError("Installed notice companions do not match the release")
        installed_owner_receipt = _qualify_installed_owner_backup_recovery(
            owner_executable=target / _REQUIRED_PAYLOAD[1],
            workspace=target.parent,
            timeout_seconds=timeout_seconds,
        )
        # All owner protocol contexts have shut down and waited for process exit here.
        _require_installed_dependency_notices(target, expected_notices)
        if (
            expected_companions
            and read_notice_companions(
                target / "licenses",
                include_covered_source=covered_source_required(expected_notices),
            )
            != expected_companions
        ):
            raise ReleaseArtifactError("Installed notice companions changed after owner exit")
    except BaseException as error:
        primary_error = error
    finally:
        try:
            if registration is not None:
                if _require_isolated_registration(target) != registration:
                    raise ReleaseArtifactError("NSIS isolated registration changed before cleanup")
                uninstaller = _require_owned_uninstaller(target)
                _run_checked((str(uninstaller), "/S"), timeout_seconds=timeout_seconds)
                _wait_until_removed(target, timeout_seconds=timeout_seconds)
            elif target.exists() or _uninstall_registrations():
                raise ReleaseArtifactError(
                    "NSIS install retained because isolated registration ownership was not verified"
                )
        except BaseException as error:
            cleanup_error = error

    if primary_error is not None:
        if cleanup_error is not None:
            raise ReleaseArtifactError(
                "NSIS qualification failed and the isolated install did not cleanly uninstall"
            ) from primary_error
        raise primary_error
    if cleanup_error is not None:
        raise cleanup_error
    if registration is None:
        raise ReleaseArtifactError("NSIS qualification produced no registration evidence")
    if installed_owner_receipt is None:
        raise ReleaseArtifactError("NSIS qualification produced no installed-owner evidence")

    return {
        "application_executable": _REQUIRED_PAYLOAD[0],
        "dependency_notices": {
            "matches_release": True,
            "path": DEPENDENCY_NOTICES_PATH,
            "readable_after_owner_exit": True,
            **(
                {"companions_readable_after_owner_exit": sorted(expected_companions)}
                if expected_companions
                else {}
            ),
        },
        "host": host,
        "install_directory_removed": not target.exists(),
        "installer_kind": "nsis-installer",
        "installer_name": installer.name,
        "installer_sha256": installer_record["sha256"],
        "installed_owner": installed_owner_receipt,
        "owner_executable": _REQUIRED_PAYLOAD[1],
        "product": product["name"],
        "registration_hive": registration.hive,
        "registration_removed": not _uninstall_registrations(),
        "source_commit": provenance["source_commit"],
        "source_tree": provenance["source_tree"],
        "uninstaller_executable": _REQUIRED_PAYLOAD[2],
        "version": product["version"],
        "windows_installer_version": product["windows_installer_version"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release_directory", type=Path)
    parser.add_argument("--qualified-output", type=Path)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    release_directory = arguments.release_directory.resolve(strict=True)
    qualified_output = (
        arguments.qualified_output.resolve()
        if arguments.qualified_output is not None
        else release_directory.with_name(f"{release_directory.name}-qualified")
    )
    workspace = Path(tempfile.mkdtemp(prefix="sylvia-gm-nsis-qualification-"))
    target = workspace / "install"
    try:
        receipt = qualify_nsis_install_uninstall(
            release_directory=release_directory,
            install_directory=target,
            timeout_seconds=arguments.timeout_seconds,
        )
        source_manifest = verify_release_layout(release_directory)
        qualification = cast(dict[str, str], source_manifest["qualification"]).copy()
        qualification["windows_11"] = "passed"
        qualified_manifest = assemble_qualified_release_layout(
            source=release_directory,
            output=qualified_output,
            qualification=qualification,
        )
        receipt["qualification"] = qualified_manifest["qualification"]
        receipt["qualified_release_directory"] = qualified_output.name
        workspace.rmdir()
    except Exception:
        try:
            workspace.rmdir()
        except OSError:
            print(f"Isolated qualification workspace retained: {workspace}", file=sys.stderr)
        raise
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
