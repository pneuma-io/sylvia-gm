"""Portable replacement faults use inert files and an explicit process-barrier test double."""

import hashlib
import json
from dataclasses import replace

import pytest

from sylvia_gm.application.desktop_update import replacement
from sylvia_gm.application.desktop_update.manifest import PayloadFile, UpdateError, canonical_bytes
from sylvia_gm.application.desktop_update.replacement import PortableReplacement
from tests.test_desktop_update_manifest import manifest_fixture, parse


def write_tree(root, label):
    from sylvia_gm.portable_artifact import REQUIRED_PAYLOADS

    data = {name: ("MZ " + label + ": " + name).encode() for name in REQUIRED_PAYLOADS}
    data["sylvia-portable.json"] = b'{"version":1}\n'
    data["SOURCE-INPUTS.json"] = canonical_bytes({"fixture": label})
    data["BUILD-IDENTITY.json"] = canonical_bytes({"fixture": label})
    data["ENTRY-HASHES.json"] = canonical_bytes(
        {
            name: {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
            for name, raw in sorted(data.items())
        }
    )
    for name, raw in data.items():
        target = root / name
        target.parent.mkdir(exist_ok=True, parents=True)
        target.write_bytes(raw)
    return data


def upgrade(tmp_path):
    live = tmp_path / "copy"
    old = write_tree(live, "old")
    data = live / "isolated-data"
    data.mkdir()
    (data / "authority.sqlite3").write_bytes(b"user database and exact content pins")
    (data / "config.yaml").write_bytes(b"protected provider and UI settings")
    other = tmp_path / "independent-copy"
    other.mkdir()
    (other / "keep").write_bytes(b"other playable")
    operation = tmp_path / "update-operation"
    operation.mkdir()
    new = write_tree(operation / "prepared", "new")
    manifest = replace(
        parse(manifest_fixture()),
        files=tuple(
            PayloadFile(name, len(raw), hashlib.sha256(raw).hexdigest())
            for name, raw in sorted(new.items())
        ),
    )
    return PortableReplacement(live, operation, manifest), old, new, data, other


def assert_tree(root, expected):
    for name, raw in expected.items():
        assert (root / name).read_bytes() == raw


def assert_player_data(data, other):
    assert (data / "authority.sqlite3").read_bytes() == b"user database and exact content pins"
    assert (data / "config.yaml").read_bytes() == b"protected provider and UI settings"
    assert (other / "keep").read_bytes() == b"other playable"


def test_retention_then_apply_is_idempotent_and_never_replaces_data_or_another_copy(tmp_path):
    update, old, new, data, other = upgrade(tmp_path)
    update.prepare()
    assert_tree(update.live, old)
    assert_tree(update.previous, old)
    update.apply(lambda: True)
    update.apply(lambda: True)
    assert_tree(update.live, new)
    assert_tree(update.previous, old)
    assert json.loads(update._journal.read_bytes())["phase"] == "awaiting_restart"
    assert_player_data(data, other)


@pytest.mark.parametrize("completed", [0, 1, 3, 9])
def test_lost_apply_acknowledgement_reconciles_exact_files_before_resuming(
    tmp_path, monkeypatch, completed
):
    update, old, new, data, other = upgrade(tmp_path)
    update.prepare()
    original = replacement.os.replace
    count = 0

    def interrupted(source, destination):
        nonlocal count
        if destination.is_relative_to(update.live):
            if count == completed:
                raise OSError("controlled replacement interruption")
            count += 1
        return original(source, destination)

    monkeypatch.setattr(replacement.os, "replace", interrupted)
    with pytest.raises(OSError):
        update.apply(lambda: True)
    assert json.loads(update._journal.read_bytes())["phase"] == "applying"
    assert_tree(update.previous, old)
    assert_player_data(data, other)
    monkeypatch.setattr(replacement.os, "replace", original)
    resumed = PortableReplacement(update.live, update.operation, update.manifest)
    resumed.apply(lambda: True)
    assert_tree(update.live, new)
    assert_player_data(data, other)


def test_process_barrier_blocks_every_live_mutation(tmp_path):
    update, old, _, data, other = upgrade(tmp_path)
    update.prepare()
    with pytest.raises(UpdateError) as error:
        update.apply(lambda: False)
    assert error.value.code == "application_busy"
    assert_tree(update.live, old)
    assert_player_data(data, other)


def test_external_change_during_interrupted_apply_is_never_overwritten(tmp_path, monkeypatch):
    update, _, _, data, other = upgrade(tmp_path)
    update.prepare()
    target = update.live / "Sylvia GM.exe"
    target.write_bytes(b"independent manual repair")
    with pytest.raises(UpdateError) as error:
        update.apply(lambda: True)
    assert error.value.code == "application_changed"
    assert target.read_bytes() == b"independent manual repair"
    assert_player_data(data, other)


def test_changed_backup_is_never_used_for_rollback(tmp_path):
    update, _, _, data, other = upgrade(tmp_path)
    update.prepare()
    update.apply(lambda: True)
    (update.previous / "sylvia-gm-owner.exe").write_bytes(b"swapped backup")
    with pytest.raises(UpdateError):
        update.rollback(lambda: True, lambda: None)
    assert_player_data(data, other)


def test_rollback_requires_data_compatibility_and_preserves_new_progress_on_rejection(tmp_path):
    update, old, new, data, other = upgrade(tmp_path)
    update.prepare()
    update.apply(lambda: True)

    def incompatible():
        raise UpdateError(
            "restore_choice_required", "Explain post-checkpoint data loss before restoring."
        )

    with pytest.raises(UpdateError) as rejected:
        update.rollback(lambda: True, incompatible)
    assert rejected.value.code == "restore_choice_required"
    assert_tree(update.live, new)
    assert_player_data(data, other)
    update.rollback(lambda: True, lambda: None)
    update.rollback(lambda: True, lambda: None)
    assert_tree(update.live, old)
    assert_tree(update.previous, old)
    assert json.loads(update._journal.read_bytes())["phase"] == "rolled_back"
    assert_player_data(data, other)


def test_unknown_new_destination_is_rejected_before_any_live_change(tmp_path):
    update, old, _, data, other = upgrade(tmp_path)
    extra = PayloadFile("optional.exe", 3, hashlib.sha256(b"new").hexdigest())
    (update.prepared / extra.path).write_bytes(b"new")
    (update.live / extra.path).write_bytes(b"manual file")
    update.manifest = replace(update.manifest, files=(*update.manifest.files, extra))
    with pytest.raises(UpdateError) as rejected:
        update.prepare()
    assert rejected.value.code == "unowned_file"
    assert_tree(update.live, old)
    assert (update.live / extra.path).read_bytes() == b"manual file"
    assert_player_data(data, other)


def mixed_upgrade(tmp_path):
    update, old, new, data, other = upgrade(tmp_path)
    old["licenses/retired-notice.txt"] = b"old-only notice"
    old["ENTRY-HASHES.json"] = canonical_bytes(
        {
            name: {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
            for name, raw in sorted(old.items())
            if name != "ENTRY-HASHES.json"
        }
    )
    for name in ("licenses/retired-notice.txt", "ENTRY-HASHES.json"):
        (update.live / name).write_bytes(old[name])
    for name in ("z-added.exe", "new/nested.lib"):
        raw = ("new-only " + name).encode()
        new[name] = raw
        target = update.prepared / name
        target.parent.mkdir(exist_ok=True, parents=True)
        target.write_bytes(raw)
    new["ENTRY-HASHES.json"] = canonical_bytes(
        {
            name: {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
            for name, raw in sorted(new.items())
            if name != "ENTRY-HASHES.json"
        }
    )
    (update.prepared / "ENTRY-HASHES.json").write_bytes(new["ENTRY-HASHES.json"])
    update.manifest = replace(
        update.manifest,
        files=tuple(
            PayloadFile(name, len(raw), hashlib.sha256(raw).hexdigest())
            for name, raw in sorted(new.items())
        ),
    )
    update.prepare()
    return update, old, new, data, other


@pytest.mark.parametrize(
    "boundary",
    [
        "prepared",
        "before-first",
        "after-first",
        "before-second",
        "after-second",
        "old-only",
        "complete",
    ],
)
def test_mixed_added_removed_changed_paths_rollback_each_partial_boundary(
    tmp_path, monkeypatch, boundary
):
    update, old, new, data, other = mixed_upgrade(tmp_path)
    original = replacement.os.replace
    added = ["new/nested.lib", "z-added.exe"]

    def interrupted(source, destination):
        for index, name in enumerate(added):
            position = "first" if index == 0 else "second"
            if destination == update.live / name:
                if boundary == "before-" + position:
                    raise OSError("controlled before new-only installation")
                result = original(source, destination)
                if boundary == "after-" + position:
                    raise OSError("controlled lost new-only move acknowledgement")
                return result
        result = original(source, destination)
        if boundary == "old-only" and source == update.live / "licenses/retired-notice.txt":
            raise OSError("controlled lost old-only retirement acknowledgement")
        return result

    if boundary != "prepared":
        monkeypatch.setattr(replacement.os, "replace", interrupted)
        if boundary == "complete":
            update.apply(lambda: True)
        else:
            with pytest.raises(OSError):
                update.apply(lambda: True)
        monkeypatch.setattr(replacement.os, "replace", original)
    resumed = PortableReplacement(update.live, update.operation, update.manifest)
    resumed.rollback(lambda: True, lambda: None)
    resumed.rollback(lambda: True, lambda: None)
    assert_tree(update.live, old)
    assert not any((update.live / name).exists() for name in added)
    assert_player_data(data, other)
    assert json.loads(update._journal.read_bytes())["phase"] == "rolled_back"


def test_partial_new_only_rollback_resumes_after_retaining_file(tmp_path, monkeypatch):
    update, old, _, data, other = mixed_upgrade(tmp_path)
    update.apply(lambda: True)
    original = replacement.os.replace

    def interrupted(source, destination):
        result = original(source, destination)
        if destination == update.operation / "failed-new" / "new/nested.lib":
            raise OSError("controlled lost rollback acknowledgement")
        return result

    monkeypatch.setattr(replacement.os, "replace", interrupted)
    with pytest.raises(OSError):
        update.rollback(lambda: True, lambda: None)
    monkeypatch.setattr(replacement.os, "replace", original)
    resumed = PortableReplacement(update.live, update.operation, update.manifest)
    resumed.rollback(lambda: True, lambda: None)
    resumed.rollback(lambda: True, lambda: None)
    assert_tree(update.live, old)
    assert_player_data(data, other)


@pytest.mark.parametrize(
    "tamper",
    ["missing-live-after-complete", "missing-staged-before-apply", "tampered-staged-before-apply"],
)
def test_missing_or_changed_new_only_identity_cannot_be_assumed_never_installed(tmp_path, tamper):
    update, _, new, data, other = mixed_upgrade(tmp_path)
    name = "z-added.exe"
    if tamper == "missing-live-after-complete":
        update.apply(lambda: True)
        (update.live / name).unlink()
    elif tamper == "missing-staged-before-apply":
        (update.prepared / name).unlink()
    else:
        (update.prepared / name).write_bytes(b"manual change")
    with pytest.raises(UpdateError):
        update.rollback(lambda: True, lambda: None)
    assert_player_data(data, other)
    assert json.loads(update._journal.read_bytes())["phase"] != "rolled_back"
