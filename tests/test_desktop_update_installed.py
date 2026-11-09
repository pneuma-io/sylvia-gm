"""Inert NSIS route fixtures: no installer process or real registration is read/written."""

import hashlib
import json
from copy import deepcopy
from dataclasses import replace

import pytest

from sylvia_gm.application.desktop_update.installed import InstalledReplacement, nsis_command_line
from sylvia_gm.application.desktop_update.manifest import (
    PayloadFile,
    UpdateError,
    Version,
    parse_current_recovery_manifest,
    parse_manifest,
)
from tests.test_desktop_update_manifest import accept_stub, envelope, manifest_fixture


def release(version, label):
    signed = manifest_fixture("installed")
    signed.update(version=version, owner_version=version)
    artifact = ("MZ inert " + label + " installer").encode()
    signed["artifact"] = {
        "url": "https://github.com/pneuma-io/sylvia-gm/releases/download/v"
        + version
        + "/sylvia-gm-"
        + version
        + "-setup.exe",
        "size": len(artifact),
        "sha256": hashlib.sha256(artifact).hexdigest(),
    }
    files = {row["path"]: (label + ":" + row["path"]).encode() for row in signed["files"]}
    signed["files"] = [
        {"path": name, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        for name, raw in files.items()
    ]
    parser = parse_current_recovery_manifest if label == "old" else parse_manifest
    manifest = parser(
        envelope(signed),
        current=Version.parse("0.3.0-alpha.1"),
        target="windows-x86_64",
        distribution="installed",
        verify=accept_stub,
    )
    return signed, manifest, artifact, files


def installation(tmp_path):
    _, previous, old_installer, old = release("0.3.0-alpha.1", "old")
    _, manifest, new_installer, new = release("0.3.0-alpha.2", "new")
    live, operation, data = tmp_path / "app with spaces", tmp_path / "operation", tmp_path / "data"
    live.mkdir()
    operation.mkdir()
    data.mkdir()
    (data / "authority.sqlite3").write_bytes(b"fictional user data and exact pins")
    (live / "manual.txt").write_bytes(b"unrelated user file")
    for name, raw in old.items():
        target = live / name
        target.parent.mkdir(exist_ok=True, parents=True)
        target.write_bytes(raw)
    (operation / "artifact.verified").write_bytes(new_installer)
    (operation / "previous-installer").mkdir()
    (operation / "previous-installer/artifact.verified").write_bytes(old_installer)

    class Registration:
        def __init__(self):
            self.row = {
                "DisplayName": "Sylvia GM",
                "DisplayVersion": "0.3.0-1",
                "Publisher": "pneuma-io",
                "MainBinaryName": "sylvia-gm.exe",
                "InstallLocation": '"' + str(live) + '"',
                "ProductLocation": str(live),
                "UninstallString": '"' + str(live / "uninstall.exe") + '"',
            }

        def read(self):
            return dict(self.row)

    registry, calls = Registration(), []

    def run(artifact, destination):
        assert destination == live
        selected, version = (
            (new, "0.3.0-2") if artifact == operation / "artifact.verified" else (old, "0.3.0-1")
        )
        calls.append(artifact)
        for name, raw in selected.items():
            target = destination / name
            target.parent.mkdir(exist_ok=True, parents=True)
            target.write_bytes(raw)
        registry.row["DisplayVersion"] = version
        return 0

    update = InstalledReplacement(live, operation, manifest, previous, registry, run)
    return update, old, new, registry, calls, data


def test_installed_prepare_apply_and_explicit_rollback_are_idempotent(tmp_path):
    update, old, new, _, calls, data = installation(tmp_path)
    update.prepare()
    assert calls == []
    with pytest.raises(UpdateError) as busy:
        update.apply(lambda: False)
    assert busy.value.code == "application_busy" and calls == []
    update.apply(lambda: True)
    update.apply(lambda: True)
    assert len(calls) == 1
    assert all((update.live / name).read_bytes() == raw for name, raw in new.items())
    assert json.loads(update._journal.read_bytes())["phase"] == "awaiting_restart"

    def incompatible():
        raise UpdateError("restore_choice_required", "Explicit checkpoint choice required")

    with pytest.raises(UpdateError):
        update.rollback(lambda: True, incompatible)
    assert len(calls) == 1
    update.rollback(lambda: True, lambda: None)
    update.rollback(lambda: True, lambda: None)
    assert len(calls) == 2
    assert all((update.live / name).read_bytes() == raw for name, raw in old.items())
    assert (data / "authority.sqlite3").read_bytes() == b"fictional user data and exact pins"
    assert (update.live / "manual.txt").read_bytes() == b"unrelated user file"


@pytest.mark.parametrize(
    "field,value",
    [
        ("Publisher", "another publisher"),
        ("InstallLocation", "C:/another-copy"),
        ("ProductLocation", "C:/another-copy"),
        ("DisplayVersion", "0.3.0-99"),
        ("UninstallString", "msiexec /x unrelated"),
        ("MainBinaryName", "another.exe"),
    ],
)
def test_wrong_registration_rejects_before_any_installer(tmp_path, field, value):
    update, old, _, registry, calls, _ = installation(tmp_path)
    registry.row[field] = value
    with pytest.raises(UpdateError):
        update.prepare()
    assert calls == []
    assert all((update.live / name).read_bytes() == raw for name, raw in old.items())


def test_tampered_current_recovery_installer_never_starts_new_installer(tmp_path):
    update, _, _, _, calls, _ = installation(tmp_path)
    (update.operation / "previous-installer/artifact.verified").write_bytes(b"tampered")
    with pytest.raises(UpdateError) as rejected:
        update.prepare()
    assert rejected.value.code == "artifact_mismatch" and calls == []


def test_failed_partial_installer_retains_retry_and_verifies_result_not_exit_code_only(tmp_path):
    update, _, new, registry, calls, _ = installation(tmp_path)
    update.prepare()
    original = update.run

    def failed(artifact, _destination):
        calls.append(artifact)
        name = "sylvia-gm.exe"
        (update.live / name).write_bytes(new[name])
        return 1

    update.run = failed
    with pytest.raises(UpdateError) as rejected:
        update.apply(lambda: True)
    assert rejected.value.code == "installer_failed"
    assert json.loads(update._journal.read_bytes())["phase"] == "applying"
    update.run = original
    update.apply(lambda: True)
    assert len(calls) == 2 and registry.row["DisplayVersion"] == "0.3.0-2"


def test_installer_zero_exit_with_incomplete_files_is_not_success(tmp_path):
    update, _, _, registry, _, _ = installation(tmp_path)
    update.prepare()
    update.run = lambda *_: registry.row.update(DisplayVersion="0.3.0-2") or 0
    with pytest.raises(UpdateError) as rejected:
        update.apply(lambda: True)
    assert rejected.value.code == "installation_incomplete"
    assert json.loads(update._journal.read_bytes())["phase"] != "awaiting_restart"


def test_installed_added_and_retired_files_preserve_unknown_files_through_recovery(tmp_path):
    update, old, new, _, _, _ = installation(tmp_path)
    old["licenses/retired.txt"] = b"old-only notice"
    new["licenses/added.txt"] = b"new-only notice"
    (update.live / "licenses/retired.txt").write_bytes(old["licenses/retired.txt"])

    def inventory(files):
        return tuple(
            PayloadFile(name, len(raw), hashlib.sha256(raw).hexdigest())
            for name, raw in files.items()
        )

    update.previous = replace(update.previous, files=inventory(old))
    update.manifest = replace(update.manifest, files=inventory(new))
    update.prepare()
    update.apply(lambda: True)
    assert not (update.live / "licenses/retired.txt").exists()
    assert (update.operation / "retired/licenses/retired.txt").read_bytes() == b"old-only notice"
    update.rollback(lambda: True, lambda: None)
    update.rollback(lambda: True, lambda: None)
    assert not (update.live / "licenses/added.txt").exists()
    assert all((update.live / name).read_bytes() == raw for name, raw in old.items())
    assert (update.live / "manual.txt").read_bytes() == b"unrelated user file"


def test_installed_attempt_reuses_authenticated_downloads_without_copying_installers(tmp_path):
    update, _, _, registry, calls, _ = installation(tmp_path)
    stage = update.operation / "attempts" / ("a" * 32)
    stage.mkdir(parents=True)
    prepared = InstalledReplacement(
        update.live,
        stage,
        update.manifest,
        update.previous,
        registry,
        update.run,
        artifacts=update.operation,
    )
    prepared.prepare()
    prepared.apply(lambda: True)
    prepared.confirm()
    prepared.confirm()
    assert json.loads(prepared._journal.read_bytes())["phase"] == "confirmed"
    assert len(calls) == 1
    assert not (stage / "artifact.verified").exists()
    assert not (stage / "previous-installer").exists()
    with pytest.raises(UpdateError):
        prepared.apply(lambda: True)


def test_nsis_uses_silent_update_and_exact_unquoted_last_destination_without_shell_switches(
    tmp_path,
):
    artifact = tmp_path / "verified installer.exe"
    live = tmp_path / "app with spaces"
    line = nsis_command_line(artifact, live)
    assert line.endswith(" /D=" + str(live))
    assert " /S /UPDATE /NS " in line
    assert " /R" not in line and " /NCRC" not in line and "uninstall" not in line


@pytest.mark.parametrize("version", ["0.3.0-alpha.0", "0.3.0-alpha.2", "0.3.0-alpha.1+other"])
def test_recovery_manifest_only_accepts_exact_running_version(version):
    signed, _, _, _ = release("0.3.0-alpha.1", "old")
    signed = deepcopy(signed)
    signed.update(version=version, owner_version=version)
    with pytest.raises(UpdateError) as rejected:
        parse_current_recovery_manifest(
            envelope(signed),
            current=Version.parse("0.3.0-alpha.1"),
            target="windows-x86_64",
            distribution="installed",
            verify=accept_stub,
        )
    assert rejected.value.code == "wrong_recovery_version"
    with pytest.raises(UpdateError) as equal:
        parse_manifest(
            envelope(release("0.3.0-alpha.1", "old")[0]),
            current=Version.parse("0.3.0-alpha.1"),
            target="windows-x86_64",
            distribution="installed",
            verify=accept_stub,
        )
    assert equal.value.code == "not_newer"
