"""Fresh desktop initialization migrates before bulk bundles without touching existing profiles."""

import io
import sqlite3

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.infrastructure.sqlite_authority import _CAMPAIGN_SCHEMA_VERSION, _SCHEMA_VERSION
from sylvia_gm.interfaces import owner_process
from tests.test_alpha_f3_package_publication import _archive, _clockwork
from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness
from tests.test_builder_library import LibraryProvider


def _version(database):
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as connection:
        return connection.execute('PRAGMA user_version').fetchone()[0]


def test_new_profile_finishes_exact_migrations_with_its_verified_backup(tmp_path, monkeypatch):
    database = tmp_path / 'fresh.db'
    with AuthorityOwner.open(database, 'fresh') as owner:
        assert _version(database) == _SCHEMA_VERSION
        backups = []
        original = owner._store._preserve_pre_migration_source

        def observed(version):
            assert owner.package_candidates() == ()
            backups.append(version)
            return original(version)

        monkeypatch.setattr(owner._store, '_preserve_pre_migration_source', observed)
        owner.prepare_new_campaign_storage()
        owner.prepare_new_campaign_storage()
        assert backups == [_SCHEMA_VERSION]
        assert _version(database) == _CAMPAIGN_SCHEMA_VERSION
        assert owner.campaign_index() == ()
    backup = tmp_path / 'migration-sources/fresh.pre-migration-v3.sqlite3'
    assert backup.is_file() and _version(backup) == _SCHEMA_VERSION
    with AuthorityOwner.open(backup, 'backup-proof') as reopened:
        assert reopened.campaign_index() == ()


def test_existing_profile_keeps_its_current_schema_and_package_state(tmp_path, monkeypatch):
    database = tmp_path / 'existing.db'
    with AuthorityOwner.open(database, 'existing') as owner:
        candidate = owner.install_package_archive(io.BytesIO(_archive((_clockwork(),))),
                                                install_id='existing-package')
    with AuthorityOwner.open(database, 'reopened') as owner:
        def forbidden(*_args):
            pytest.fail('Existing profiles retain their ordinary migration boundary')

        monkeypatch.setattr(owner._store, '_ensure_campaign_schema', forbidden)
        owner.prepare_new_campaign_storage()
        assert _version(database) == _SCHEMA_VERSION
        assert owner.package_candidates() == (candidate,)


def test_fresh_schema_backup_failure_leaves_retryable_predecessor(tmp_path, monkeypatch):
    database = tmp_path / 'refused.db'
    with AuthorityOwner.open(database, 'refused') as owner:
        original = owner._store._preserve_pre_migration_source

        def refused(*_args):
            raise PermissionError('synthetic owned-backup refusal')

        monkeypatch.setattr(owner._store, '_preserve_pre_migration_source', refused)
        with pytest.raises(PermissionError, match='synthetic'):
            owner.prepare_new_campaign_storage()
        assert _version(database) == _SCHEMA_VERSION
        assert owner.campaign_index() == () and owner.package_candidates() == ()
        monkeypatch.setattr(owner._store, '_preserve_pre_migration_source', original)
        owner.prepare_new_campaign_storage()
        assert _version(database) == _CAMPAIGN_SCHEMA_VERSION


def test_actual_owner_startup_prepares_fresh_schema_before_bundles(tmp_path, monkeypatch):
    database = tmp_path / 'owner.db'
    observed = []

    def bundled(_bus):
        observed.append(_version(database))

    monkeypatch.setattr(owner_process, '_preinstall_bundled_srd', bundled)
    monkeypatch.setattr(owner_process, '_preinstall_bundled_riichi', bundled)
    harness = _OwnerHarness(database, LibraryProvider(responses=[]))
    try:
        assert observed == [_CAMPAIGN_SCHEMA_VERSION, _CAMPAIGN_SCHEMA_VERSION]
        assert harness.request('empty', 'campaign_index') == []
    finally:
        harness.close()


def test_fresh_migration_failure_rolls_back_and_can_retry(tmp_path, monkeypatch):
    database = tmp_path / 'rollback.db'
    with AuthorityOwner.open(database, 'rollback') as owner:
        original = owner._store._apply_effort_migration

        def failed(connection):
            original(connection)
            raise RuntimeError('synthetic final migration failure')

        monkeypatch.setattr(owner._store, '_apply_effort_migration', failed)
        with pytest.raises(RuntimeError, match='synthetic final migration'):
            owner.prepare_new_campaign_storage()
        assert _version(database) == _SCHEMA_VERSION
        assert owner.campaign_index() == () and owner.package_candidates() == ()
        monkeypatch.setattr(owner._store, '_apply_effort_migration', original)
        owner.prepare_new_campaign_storage()
        assert _version(database) == _CAMPAIGN_SCHEMA_VERSION
