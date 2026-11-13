"""Safe saved-file operations: the PL-22 first native path and its failure preservation.

The journey and failure tests run on every supported platform through the real
store, so Windows keeps its regression coverage while Linux proves the adapter.
Linux-only tests exercise descriptor identity, link refusal, tmpfs refusal and
directory durability. On Linux, run with ``--basetemp`` on ext4, xfs or btrfs:
other filesystems are deliberately refused, which these tests report as a failure
of the environment rather than skipping.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate
from sylvia_gm.domain.authority import PackageLock, WriterRole
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.session import SessionMessageKind
from sylvia_gm.infrastructure import campaign_archive, safe_file
from sylvia_gm.infrastructure.sqlite_authority import (
    _CAMPAIGN_SCHEMA_VERSION,
    _SCHEMA_VERSION,
    SQLiteAuthorityStore,
)

LINUX = sys.platform.startswith("linux")
linux_only = pytest.mark.skipif(not LINUX, reason="Linux descriptor semantics")
Publication = safe_file.Publication


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    refusal = safe_file.files.volume_admission(tmp_path / "probe")
    if refusal is not None:
        pytest.fail(
            f"tmp_path filesystem is refused ({refusal}); rerun with --basetemp on "
            "a supported local filesystem"
        )
    return tmp_path


def _version(database: Path) -> int:
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])


def _campaign(owner: AuthorityOwner, campaign_id: str) -> None:
    owner.create_campaign(
        CampaignCreate(
            display_name=f"Synthetic {campaign_id}",
            writer=WriterRole.BUILDER,
            package_lock=PackageLock(entries=()),
            turn_id=f"turn-{campaign_id}",
            campaign_id=campaign_id,
        )
    )
    owner.complete_turn(campaign_id, f"turn-{campaign_id}")


def _temporaries(root: Path) -> list[Path]:
    return [path for path in root.rglob("*") if ".backup-" in path.name]


def _seeded(owner: AuthorityOwner) -> None:
    owner.prepare_new_campaign_storage()
    owner.create_builder_chat(
        provider_id="synthetic", model_id="synthetic-model", session_id="builder",
    )
    owner.append_agent_message(
        session_id="builder", message_id="draft",
        kind=SessionMessageKind.TALK, content={"text": "Synthetic saved draft."},
    )
    _campaign(owner, "journey")


def _publish_then_report(outcome: Publication):
    """Perform the real publication, then report ``outcome`` as the platform result."""

    real = SQLiteAuthorityStore._publish_backup_file

    def publish(source: Path, target: Path, parent_identity: tuple[int, int]) -> Publication:
        assert real(source, target, parent_identity) is Publication.PUBLISHED
        return outcome

    return staticmethod(publish)


def _refuse_publication(source: Path, target: Path, parent_identity: tuple[int, int]):
    return Publication.NOT_PUBLISHED


# --- The first native path --------------------------------------------------------------


def test_first_native_path_prepares_reopens_backs_up_exports_and_migrates(
    workspace: Path,
) -> None:
    profile = workspace / "profile"
    profile.mkdir()
    database = profile / "sylvia.db"
    backup = workspace / "exports" / "sylvia-backup.db"
    archive = workspace / "exports" / "journey.sylvia-gm-campaign"
    backup.parent.mkdir()

    with AuthorityOwner.open(database, "first-start") as owner:
        _seeded(owner)
        saved = owner.snapshot("journey")
        chats = owner.builder_chat_index()
        context = owner.session_context("builder")
    assert _version(database) == _CAMPAIGN_SCHEMA_VERSION

    with AuthorityOwner.open(database, "restart") as owner:
        assert owner.snapshot("journey") == saved
        assert owner.builder_chat_index() == chats
        assert owner.session_context("builder") == context
        owner.backup_database(backup)
        exported = owner.export_campaign(
            operation_id="export", campaign_id="journey", target=archive,
        )

    predecessor = profile / "migration-sources" / "sylvia.pre-migration-v3.sqlite3"
    assert _version(predecessor) == _SCHEMA_VERSION
    with AuthorityOwner.open(predecessor, "predecessor") as reopened:
        assert reopened.campaign_index() == ()
    with AuthorityOwner.open(backup, "backup") as reopened:
        assert reopened.snapshot("journey") == saved
        assert reopened.builder_chat_index() == chats
        assert reopened.session_context("builder") == context
    campaign_archive.validate_campaign_archive(archive, expected_campaign_id="journey")
    assert exported.byte_count == archive.stat().st_size
    with AuthorityOwner.open(workspace / "recovered.db", "recovered") as recovered:
        recovered.recover_campaign(recovery_id="recover", source=archive)
        assert recovered.snapshot("journey") == saved
    assert _temporaries(workspace) == []


# --- Failure preservation on every platform -----------------------------------------------


def test_refused_backup_publication_keeps_prior_backup_and_leaves_no_temporary(
    workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    backup = workspace / "backup.db"
    with AuthorityOwner.open(workspace / "source.db", "source") as owner:
        _campaign(owner, "kept")
        owner.backup_database(backup)
        prior = backup.read_bytes()
        _campaign(owner, "later")
        with monkeypatch.context() as patch:
            patch.setattr(
                SQLiteAuthorityStore, "_publish_backup_file", staticmethod(_refuse_publication)
            )
            with pytest.raises(CommandRejected) as rejected:
                owner.backup_database(backup)
        assert rejected.value.code == "backup_publication_replace_failed"
        assert backup.read_bytes() == prior
        assert _temporaries(workspace) == []
        owner.backup_database(backup)
    with AuthorityOwner.open(backup, "retried") as reopened:
        assert {entry.campaign_id for entry in reopened.campaign_index()} == {"kept", "later"}


def test_uncertain_backup_durability_is_reported_not_claimed_as_success(
    workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    backup = workspace / "backup.db"
    with AuthorityOwner.open(workspace / "source.db", "source") as owner:
        _campaign(owner, "uncertain")
        monkeypatch.setattr(
            SQLiteAuthorityStore,
            "_publish_backup_file",
            _publish_then_report(Publication.DURABILITY_UNCERTAIN),
        )
        with pytest.raises(CommandRejected) as rejected:
            owner.backup_database(backup)
    assert rejected.value.code == "backup_publication_durability_uncertain"
    with AuthorityOwner.open(backup, "retained") as reopened:
        assert [entry.campaign_id for entry in reopened.campaign_index()] == ["uncertain"]
    assert _temporaries(workspace) == []


def test_uncertain_pre_migration_backup_stops_migration_and_can_retry(
    workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = workspace / "profile.db"
    with AuthorityOwner.open(database, "profile") as owner:
        with monkeypatch.context() as patch:
            patch.setattr(
                SQLiteAuthorityStore,
                "_publish_backup_file",
                _publish_then_report(Publication.DURABILITY_UNCERTAIN),
            )
            with pytest.raises(CommandRejected) as rejected:
                owner.prepare_new_campaign_storage()
        assert rejected.value.code == "backup_publication_durability_uncertain"
        assert _version(database) == _SCHEMA_VERSION
        owner.prepare_new_campaign_storage()
        assert _version(database) == _CAMPAIGN_SCHEMA_VERSION
    assert _temporaries(workspace) == []


@pytest.mark.parametrize(
    ("publication", "code"),
    [
        (Publication.NOT_PUBLISHED, "portable_export_move_file_ex_failed"),
        (Publication.DURABILITY_UNCERTAIN, "portable_export_publication_durability_uncertain"),
    ],
)
def test_export_publication_outcomes_preserve_or_retain_honestly(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, publication: Publication, code: str,
) -> None:
    archive = workspace / "campaign.sylvia-gm-campaign"
    with AuthorityOwner.open(workspace / "source.db", "source") as owner:
        owner.prepare_new_campaign_storage()
        _campaign(owner, "exported")
        owner.export_campaign(operation_id="first", campaign_id="exported", target=archive)
        prior = archive.read_bytes()
        replacement = (
            staticmethod(_refuse_publication)
            if publication is Publication.NOT_PUBLISHED
            else _publish_then_report(publication)
        )
        with monkeypatch.context() as patch:
            patch.setattr(SQLiteAuthorityStore, "_publish_backup_file", replacement)
            with pytest.raises(CommandRejected) as rejected:
                owner.export_campaign(
                    operation_id="second", campaign_id="exported", target=archive,
                    include_thinking=True,
                )
    assert rejected.value.code == code
    if publication is Publication.NOT_PUBLISHED:
        assert archive.read_bytes() == prior
    else:
        assert archive.read_bytes() != prior
        campaign_archive.validate_campaign_archive(archive, expected_campaign_id="exported")
    assert _temporaries(workspace) == []


def test_temporary_claim_never_reuses_an_existing_name(
    workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    backup = workspace / "backup.db"
    foreign = workspace / ".backup.db.backup-foreign.tmp"
    foreign.write_bytes(b"not ours")
    real = SQLiteAuthorityStore._backup_temp_candidate
    offered: list[Path] = []

    def candidate(store: SQLiteAuthorityStore, target: Path) -> Path:
        offered.append(foreign if not offered else real(store, target))
        return offered[-1]

    monkeypatch.setattr(SQLiteAuthorityStore, "_backup_temp_candidate", candidate)
    with AuthorityOwner.open(workspace / "source.db", "source") as owner:
        _campaign(owner, "claimed")
        owner.backup_database(backup)
    assert len(offered) == 2
    assert foreign.read_bytes() == b"not ours"
    assert [path.name for path in _temporaries(workspace)] == [foreign.name]


def test_failure_after_claim_removes_only_the_owned_temporary(
    workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    backup = workspace / "backup.db"
    with AuthorityOwner.open(workspace / "source.db", "source") as owner:
        _campaign(owner, "verified")
        owner.backup_database(backup)
        prior = backup.read_bytes()

        def unverifiable(_store: SQLiteAuthorityStore, _connection: sqlite3.Connection) -> None:
            raise RuntimeError("synthetic verification failure")

        monkeypatch.setattr(SQLiteAuthorityStore, "_verify_backup_authority", unverifiable)
        with pytest.raises(CommandRejected) as rejected:
            owner.backup_database(backup)
    assert rejected.value.code == "backup_verification_failed"
    assert getattr(rejected.value, "supplemental_codes", ()) == ()
    assert backup.read_bytes() == prior
    assert _temporaries(workspace) == []


@pytest.mark.parametrize("suffix", ["", "-wal", ".owner.lock"])
def test_backup_refuses_the_live_database_and_its_artifacts(
    workspace: Path, suffix: str,
) -> None:
    database = workspace / "source.db"
    with AuthorityOwner.open(database, "source") as owner:
        _campaign(owner, "aliased")
        before = database.read_bytes()
        with pytest.raises(CommandRejected) as rejected:
            owner.backup_database(Path(f"{database}{suffix}"))
    assert rejected.value.code == "backup_target_envelope_source_alias"
    assert database.read_bytes() == before


# --- Platform-neutral adapter units ----------------------------------------------------------


_MOUNTINFO = "\n".join(
    (
        "22 1 8:1 / / rw,relatime shared:1 - ext4 /dev/sda1 rw",
        "30 22 0:25 / /dev/shm rw,nosuid shared:2 - tmpfs tmpfs rw",
        "31 22 0:26 / /home rw shared:3 - btrfs /dev/sda2 rw,subvol=/home",
        "32 31 0:40 / /home/me/My\\040Share rw - cifs //server/share rw",
        "33 22 0:41 / /mnt/data rw - xfs /dev/sdb1 rw",
        "34 22 0:42 / /mnt/data rw - nfs4 server:/data rw",
        "malformed line without separator",
    )
)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/home/me/campaigns/sylvia.db", "btrfs"),
        ("/home/me/My Share/sylvia.db", "cifs"),
        ("/home/me/My Shared/sylvia.db", "btrfs"),
        ("/dev/shm/sylvia.db", "tmpfs"),
        ("/var/lib/sylvia.db", "ext4"),
        ("/mnt/data/sylvia.db", "nfs4"),
        ("/mnt/database", "ext4"),
    ],
)
def test_mount_type_uses_longest_match_and_latest_shadowing_mount(
    path: str, expected: str,
) -> None:
    assert safe_file.mount_filesystem_type(_MOUNTINFO, path) == expected


@pytest.mark.parametrize(
    ("filesystem", "admission"),
    [
        ("ext4", None),
        ("xfs", None),
        ("btrfs", None),
        ("nfs4", "remote"),
        ("cifs", "remote"),
        ("9p", "remote"),
        ("fuse.sshfs", "remote"),
        ("tmpfs", "unsupported"),
        ("overlay", "unsupported"),
        ("vfat", "unsupported"),
        ("fuseblk", "unsupported"),
        (None, "unknown"),
    ],
)
def test_only_local_journalling_filesystems_are_admitted(
    filesystem: str | None, admission: str | None,
) -> None:
    assert safe_file.classify_filesystem(filesystem) == admission


def test_unsupported_platforms_refuse_before_touching_the_filesystem(tmp_path: Path) -> None:
    unsupported = safe_file.SafeFiles()
    target = tmp_path / "backup.db"
    assert not unsupported.lexical_backup_target_supported(target)
    assert not unsupported.lexical_export_target_supported(target)
    assert unsupported.volume_admission(target) == "unsupported"
    assert list(tmp_path.iterdir()) == []


# --- Linux descriptor semantics --------------------------------------------------------------


@linux_only
def test_linux_refuses_link_targets_and_hardlink_aliases(workspace: Path) -> None:
    database = workspace / "source.db"
    pointee = workspace / "pointee.db"
    pointee.write_bytes(b"pointee")
    symlink = workspace / "symlink.db"
    symlink.symlink_to(pointee)
    linked_parent = workspace / "linked-parent"
    linked_parent.symlink_to(workspace, target_is_directory=True)
    with AuthorityOwner.open(database, "source") as owner:
        _campaign(owner, "links")
        hardlink = workspace / "hardlink.db"
        os.link(database, hardlink)
        cases = {
            symlink: "backup_target_envelope_attributes_unsupported",
            linked_parent / "backup.db": "backup_target_envelope_attributes_unsupported",
            hardlink: "backup_target_envelope_source_alias",
        }
        for target, code in cases.items():
            with pytest.raises(CommandRejected) as rejected:
                owner.backup_database(target)
            assert rejected.value.code == code, target
        with pytest.raises(CommandRejected) as rejected:
            owner.export_campaign(operation_id="link", campaign_id="links", target=symlink)
        assert rejected.value.code == "portable_export_destination_reparse"
    assert pointee.read_bytes() == b"pointee"
    assert symlink.is_symlink()
    assert _temporaries(workspace) == []


@linux_only
def test_linux_refuses_tmpfs_destinations(workspace: Path) -> None:
    shm = Path("/dev/shm")
    if safe_file.mount_filesystem_type(
        safe_file.files._read_mountinfo(), os.path.realpath(shm)
    ) != "tmpfs":
        pytest.skip("/dev/shm is not tmpfs in this environment")
    with AuthorityOwner.open(workspace / "source.db", "source") as owner:
        _campaign(owner, "volatile")
        with pytest.raises(CommandRejected) as rejected:
            owner.backup_database(shm / f"sylvia-{os.getpid()}.db")
        assert rejected.value.code == "backup_target_envelope_volume_unsupported"
        with pytest.raises(CommandRejected) as rejected:
            owner.export_campaign(
                operation_id="tmpfs", campaign_id="volatile",
                target=shm / f"sylvia-{os.getpid()}.zip",
            )
        assert rejected.value.code == "portable_export_destination_unsupported"
    assert not list(shm.glob(f"*sylvia-{os.getpid()}*"))


@linux_only
def test_linux_temporary_is_private_and_never_follows_a_planted_link(
    workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    backup = workspace / "backup.db"
    pointee = workspace / "pointee"
    pointee.write_bytes(b"pointee")
    planted = workspace / ".backup.db.backup-planted.tmp"
    planted.symlink_to(pointee)
    real = SQLiteAuthorityStore._backup_temp_candidate
    real_copy = SQLiteAuthorityStore._copy_online_backup
    offered: list[Path] = []
    modes: list[int] = []

    def candidate(store: SQLiteAuthorityStore, target: Path) -> Path:
        offered.append(planted if not offered else real(store, target))
        return offered[-1]

    def copy(store: SQLiteAuthorityStore, temporary: Path) -> None:
        modes.append(os.lstat(temporary).st_mode & 0o777)
        real_copy(store, temporary)

    monkeypatch.setattr(SQLiteAuthorityStore, "_backup_temp_candidate", candidate)
    monkeypatch.setattr(SQLiteAuthorityStore, "_copy_online_backup", copy)
    with AuthorityOwner.open(workspace / "source.db", "source") as owner:
        _campaign(owner, "private")
        owner.backup_database(backup)
    assert len(offered) == 2 and modes == [0o600]
    assert pointee.read_bytes() == b"pointee" and planted.is_symlink()


@linux_only
def test_linux_cleanup_never_unlinks_a_file_that_took_the_claimed_name(
    workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unverifiable(_store: SQLiteAuthorityStore, _connection: sqlite3.Connection) -> None:
        raise RuntimeError("synthetic verification failure")

    def swap(_store: SQLiteAuthorityStore, path: Path) -> None:
        impostor = path.with_name("impostor")
        impostor.write_bytes(b"foreign")
        os.replace(impostor, path)

    monkeypatch.setattr(SQLiteAuthorityStore, "_verify_backup_authority", unverifiable)
    monkeypatch.setattr(SQLiteAuthorityStore, "_before_backup_main_cleanup_disposition", swap)
    with AuthorityOwner.open(workspace / "source.db", "source") as owner:
        _campaign(owner, "swapped")
        with pytest.raises(CommandRejected) as rejected:
            owner.backup_database(workspace / "backup.db")
    assert rejected.value.code == "backup_verification_failed"
    assert "backup_cleanup_main_disposition_failed" in rejected.value.supplemental_codes
    [survivor] = _temporaries(workspace)
    assert survivor.read_bytes() == b"foreign"


@linux_only
def test_linux_publication_syncs_the_pinned_directory(
    workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    synced: list[tuple[int, int]] = []
    real_sync = safe_file._LinuxFiles._sync_directory

    def sync(files: safe_file.SafeFiles, directory: int) -> None:
        held = os.fstat(directory)
        synced.append((held.st_dev, held.st_ino))
        real_sync(files, directory)

    monkeypatch.setattr(safe_file._LinuxFiles, "_sync_directory", sync)
    exports = workspace / "exports"
    exports.mkdir()
    with AuthorityOwner.open(workspace / "source.db", "source") as owner:
        _campaign(owner, "durable")
        owner.backup_database(exports / "backup.db")
        owner.export_campaign(
            operation_id="durable", campaign_id="durable", target=exports / "durable.zip",
        )
    held = os.stat(exports)
    assert synced == [(held.st_dev, held.st_ino)] * 2


@linux_only
def test_linux_failed_directory_sync_reports_uncertain_durability(
    workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failed(_files: safe_file.SafeFiles, _directory: int) -> None:
        raise OSError("synthetic directory sync failure")

    backup = workspace / "backup.db"
    monkeypatch.setattr(safe_file._LinuxFiles, "_sync_directory", failed)
    with AuthorityOwner.open(workspace / "source.db", "source") as owner:
        _campaign(owner, "renamed")
        with pytest.raises(CommandRejected) as rejected:
            owner.backup_database(backup)
    assert rejected.value.code == "backup_publication_durability_uncertain"
    with AuthorityOwner.open(backup, "renamed-copy") as reopened:
        assert [entry.campaign_id for entry in reopened.campaign_index()] == ["renamed"]
    assert _temporaries(workspace) == []


@linux_only
def test_linux_publication_refuses_a_replaced_parent_directory(workspace: Path) -> None:
    files = safe_file.files
    parent = workspace / "parent"
    parent.mkdir()
    source = parent / "source.tmp"
    source.write_bytes(b"new")
    held = os.stat(parent)
    moved = workspace / "moved"
    parent.rename(moved)
    parent.mkdir()
    (parent / "source.tmp").write_bytes(b"other")
    outcome = files.publish_replace(
        source, parent / "target", parent_identity=(held.st_dev, held.st_ino),
    )
    assert outcome is Publication.NOT_PUBLISHED
    assert not (parent / "target").exists()
    assert (moved / "source.tmp").read_bytes() == b"new"
