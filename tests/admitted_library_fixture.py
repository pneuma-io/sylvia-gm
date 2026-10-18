"""Reuse an admitted, campaign-free library while isolating every scenario database.

The normal package importer validates each distinct archive byte sequence once per
worker. No mechanic, schema check, action, participant or campaign state is mocked.
Admission-specific tests keep calling the importer directly.
"""

import hashlib
import io
import json
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from scripts.journey_progress import phase, timed

from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate
from sylvia_gm.domain.authority import PackageLock, WriterRole

CANDIDATE_ID = "candidate-fixture-package"


@lru_cache(maxsize=4)
@timed("package_seed_prepare")
def _admitted_seed(archive: bytes) -> Path:
    root = Path(tempfile.mkdtemp(prefix="sylvia-admitted-library-"))
    database = root / "closed-library.sqlite3"
    with AuthorityOwner.open(database, "fixture-library-seed") as owner:
        # Activate the current campaign schema while the database is tiny.
        # Otherwise each isolated scenario legitimately backs up and verifies the
        # entire large library during its first campaign-schema migration.
        owner.create_campaign(
            CampaignCreate(
                campaign_id="fixture-schema-bootstrap",
                display_name="Empty fixture schema bootstrap",
                writer=WriterRole.DM,
                package_lock=PackageLock(entries=()),
                turn_id="bootstrap",
            )
        )
        owner.trash_campaign(
            operation_id="trash-bootstrap",
            campaign_id="fixture-schema-bootstrap",
            expected_revision=0,
        )
        owner.purge_campaign(
            operation_id="purge-bootstrap",
            campaign_id="fixture-schema-bootstrap",
            expected_revision=1,
            confirmation="permanently purge fixture-schema-bootstrap",
        )
        candidate = owner.install_package_archive(io.BytesIO(archive), install_id="fixture-package")
        assert candidate.candidate_id == CANDIDATE_ID
    # The sole owner is closed. Seal its own seed before copying it into
    # fresh scenarios instead of retaining a second identical library database.
    with sqlite3.connect(database) as source:
        checkpoint = source.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if checkpoint[0] != 0 or checkpoint[1] != checkpoint[2]:
            raise RuntimeError("The owned admission seed did not checkpoint completely")
    (root / "receipt.json").write_text(
        json.dumps(
            {
                "archive_sha256": hashlib.sha256(archive).hexdigest(),
                "library_sha256": hashlib.sha256(database.read_bytes()).hexdigest(),
                "admission": "normal importer, runtime and content validation",
                "scenario_campaigns_created": False,
                "empty_schema_bootstrap_purged": True,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return database


def prepare_library_database(database: Path, archive: bytes) -> None:
    """Copy only into a fresh scenario path; never overwrite saved data."""
    if database.exists():
        raise ValueError("An admitted fixture needs a fresh isolated database")
    with phase("package_seed_lookup"):
        seed = _admitted_seed(archive)
    database.parent.mkdir(parents=True, exist_ok=True)
    with phase("database_copy"):
        shutil.copyfile(seed, database)


@contextmanager
def admitted_fixture_owner(database: Path, owner_id: str, archive: bytes):
    prepare_library_database(database, archive)
    with AuthorityOwner.open(database, owner_id) as owner:
        yield owner, owner.package_candidate(CANDIDATE_ID)
