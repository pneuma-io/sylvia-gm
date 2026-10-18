"""Interrupted private staging cannot strand a save or publish incomplete bytes."""

import hashlib

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.errors import SchemaIdentityError
from sylvia_gm.infrastructure import sqlite_packages
from sylvia_gm.infrastructure.package_archive import PACKAGE_CHUNK_LIMIT
from tests.test_alpha_f3_package_publication import _clockwork, _install


def interrupt(owner, monkeypatch, committed_chunks):
    original = sqlite_packages.append_staging_chunk
    calls = 0

    def append(connection, **kwargs):
        nonlocal calls
        if calls == committed_chunks:
            raise OSError("synthetic interrupted package write")
        calls += 1
        return original(connection, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(sqlite_packages, "append_staging_chunk", append)
        with pytest.raises(OSError, match="interrupted package"):
            _install(owner, (_clockwork(payload=b"x" * (PACKAGE_CHUNK_LIMIT + 17)),), "interrupted")
    assert owner.package_candidates() == ()
    assert owner.package_staging("interrupted").status == "staging"


@pytest.mark.parametrize("committed_chunks", [0, 1])
def test_interrupted_staging_reopens_and_retries_without_publishing_partial_bytes(
    tmp_path, monkeypatch, committed_chunks
):
    db = tmp_path / "authority.sqlite3"
    with AuthorityOwner.open(db, "staging-proof") as owner:
        interrupt(owner, monkeypatch, committed_chunks)
    with AuthorityOwner.open(db, "staging-reopen") as owner:
        assert owner.package_candidates() == ()
        candidate = _install(
            owner, (_clockwork(payload=b"x" * (PACKAGE_CHUNK_LIMIT + 17)),), "interrupted"
        )
        assert owner.package_candidate(candidate.candidate_id) == candidate
        assert owner.package_staging("interrupted").status == "published"
    with AuthorityOwner.open(db, "published-reopen") as owner:
        assert owner.package_candidate(candidate.candidate_id) == candidate


@pytest.mark.parametrize("tamper", ["chunk_hash", "oversized_prefix", "premature_publication"])
def test_interrupted_staging_does_not_weaken_integrity_checks(tmp_path, monkeypatch, tamper):
    db = tmp_path / "authority.sqlite3"
    with AuthorityOwner.open(db, "staging-proof") as owner:
        interrupt(owner, monkeypatch, 1)
        connection = owner._store._connect()
        try:
            if tamper == "chunk_hash":
                connection.execute("UPDATE package_staging_chunks SET data=X'00'")
            elif tamper == "oversized_prefix":
                content = b"x" * (PACKAGE_CHUNK_LIMIT + 18)
                connection.execute(
                    "UPDATE package_staging_chunks SET data=?, chunk_sha256=?",
                    (content, hashlib.sha256(content).hexdigest()),
                )
            else:
                connection.execute("UPDATE package_staging SET status='published'")
        finally:
            connection.close()
    with pytest.raises(SchemaIdentityError):
        AuthorityOwner.open(db, "invalid-staging-reopen")
