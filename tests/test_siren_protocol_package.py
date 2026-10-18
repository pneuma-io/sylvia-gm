"""Setting composition uses normal containment, retained sources and library retrieval."""

import io
import json

import pytest
from scripts.build_siren_protocol_package import (
    RULESET_ID,
    SETTING_ID,
    compose_siren_protocol_archive,
)

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.builder_references import capture_content_references
from sylvia_gm.builder._ruleset_source import compile_ruleset_source
from sylvia_gm.domain.authority import PackageRole
from sylvia_gm.interfaces.owner_process import _campaign_creation_catalog


def ruleset_fixture():
    """A public mechanics-free fixture proves containment without redistributing TD."""
    return compile_ruleset_source(
        json.dumps(
            {
                "format": 1,
                "id": RULESET_ID,
                "version": "9.0.0",
                "label": "Composition fixture",
                "publisher": "Test",
                "mechanic_families": {},
                "public_actions": [],
                "private_mechanics": [],
                "definitions": {},
                "participant_artifacts": [],
                "operating_guide": None,
            }
        ).encode()
    ).draft


def check_owner_journey(tmp_path, draft):
    database = tmp_path / "library.db"
    with (
        compose_siren_protocol_archive(dict(draft.record), dict(draft.payloads)) as artifact,
        AuthorityOwner.open(database, "siren-import") as owner,
    ):
        candidate = owner.install_package_archive(artifact.archive, install_id="siren")
        primary = next(
            item for item in candidate.package_lock.entries if item.role is PackageRole.PRIMARY
        )
        setting = next(
            item for item in candidate.package_lock.entries if item.package_id == SETTING_ID
        )
        record = owner.package_record(setting.package_id, setting.version, setting.sha256)
        assert record["metadata"]["label"] == "Siren Protocol"
        assert record["dependencies"][0]["content_sha256"] == primary.sha256
        for path, expected in (
            ("setting/locations/SYDNEY_DISTRICTS_PHASE1.md", "Sydney"),
            ("setting/factions/AUSTRALIAN_OLIGARCHIES.md", "PALISADE"),
            ("characters/Hex.md", "Hex"),
            ("implementation/equipment.json", "palisade"),
        ):
            assert (
                expected.casefold()
                in owner.package_text_file(
                    setting.package_id,
                    setting.version,
                    setting.sha256,
                    path,
                ).casefold()
            )
        references = capture_content_references(
            owner,
            tmp_path,
            _campaign_creation_catalog(tmp_path, owner),
            ruleset_id=candidate.candidate_id,
            setting_id=SETTING_ID,
            setting_candidate_id=candidate.candidate_id,
        )
        assert {item["id"] for item in references["packages"]} == {RULESET_ID, SETTING_ID}
        output = io.BytesIO()
        owner.export_package_candidate(candidate.candidate_id, output)
    with AuthorityOwner.open(database, "siren-reopen") as owner:
        reopened = owner.package_candidate(candidate.candidate_id)
        assert reopened.closure_sha256 == candidate.closure_sha256
    with AuthorityOwner.open(tmp_path / "reimport.db", "siren-reimport") as owner:
        imported = owner.install_package_archive(
            io.BytesIO(output.getvalue()), install_id="siren-again"
        )
        assert imported.package_lock == candidate.package_lock


def test_setting_library_reopen_export_and_builder_references(tmp_path):
    check_owner_journey(tmp_path, ruleset_fixture())


def test_snapshot_rejects_old_inverted_ownership():
    draft = ruleset_fixture()
    with pytest.raises(ValueError, match="standalone"):
        compose_siren_protocol_archive(
            {**draft.record, "dependencies": [{"id": SETTING_ID}]}, dict(draft.payloads)
        )
