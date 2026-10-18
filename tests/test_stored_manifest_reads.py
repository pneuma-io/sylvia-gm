"""Repeated package reads reuse parsing without hiding changed authority bytes."""

import json
import sqlite3
from dataclasses import FrozenInstanceError

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.infrastructure import sqlite_packages
from tests.test_alpha_f3_package_publication import _clockwork, _install


@pytest.fixture
def published_manifest(tmp_path):
    database = tmp_path / "manifest.sqlite3"
    with AuthorityOwner.open(database, "manifest-read-test") as owner:
        candidate = _install(owner, (_clockwork(),), "manifest-read-install")
    primary = candidate.package_lock.entries[0]
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        yield connection, (primary.package_id, primary.version, primary.sha256)
    finally:
        connection.close()


def test_repeated_published_manifest_reads_do_not_repeat_validation(
    published_manifest, monkeypatch,
):
    connection, identity = published_manifest
    original = sqlite_packages.manifest_from_record
    parsed = []

    def count_parse(record):
        parsed.append(None)
        return original(record)

    monkeypatch.setattr(sqlite_packages, "manifest_from_record", count_parse)
    manifests = [sqlite_packages.exact_manifest(connection, identity) for _ in range(8)]
    assert len(parsed) <= 1
    assert all(manifest == manifests[0] for manifest in manifests)
    with pytest.raises(FrozenInstanceError):
        manifests[0].version = "changed"
    with pytest.raises(TypeError):
        manifests[0].tools[0].input_schema["properties"]["steps"]["type"] = "string"
    assert sqlite_packages.exact_manifest(connection, identity).tools[0].input_schema[
        "properties"
    ]["steps"]["type"] == "integer"


def test_changed_manifest_bytes_are_revalidated_after_a_successful_read(published_manifest):
    connection, identity = published_manifest
    manifest = sqlite_packages.exact_manifest(connection, identity)
    record = json.loads(manifest.canonical_json)
    record["tools"][0]["input_schema"]["properties"]["steps"]["type"] = "string"
    # Deliberately corrupt the stored record without updating its content identity.
    connection.execute(
        "UPDATE package_manifests SET canonical_json = ? WHERE package_id = ?",
        (sqlite_packages._dump(record), identity[0]),
    )
    with pytest.raises(ValueError):
        sqlite_packages.exact_manifest(connection, identity)


@pytest.mark.parametrize("change", ["unpublish", "delete"])
def test_manifest_availability_is_read_again_after_a_successful_read(published_manifest, change):
    connection, identity = published_manifest
    sqlite_packages.exact_manifest(connection, identity)
    if change == "unpublish":
        connection.execute(
            "UPDATE package_manifests SET status = 'reclaimed' WHERE package_id = ?",
            (identity[0],),
        )
    else:
        connection.execute("DELETE FROM package_manifests WHERE package_id = ?", (identity[0],))
    with pytest.raises(CommandRejected, match="Exact package manifest is unavailable"):
        sqlite_packages.exact_manifest(connection, identity)
