from __future__ import annotations

import json
import zipfile
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.builder._package_materializer import materialize_ruleset_draft
from sylvia_gm.domain._content_schema_declarations import CONTENT_DECLARATIONS_SCHEMA
from sylvia_gm.domain._content_schema_package import PACKAGE_ARCHIVE_MANIFEST_SCHEMA
from sylvia_gm.domain.content_definitions import get_content_definition
from sylvia_gm.domain.package_library import parse_content_declarations_bytes
from sylvia_gm.infrastructure import campaign_archive
from tests.test_alpha_f5_smoke_backup import _campaign, _trigger_campaign_schema
from tests.test_character_creation_contract import _draft


def test_materialized_package_manifest_matches_retrievable_structure(tmp_path: Path) -> None:
    _draft(tmp_path)
    with (
        materialize_ruleset_draft(tmp_path) as materialized,
        zipfile.ZipFile(materialized.archive) as archive,
    ):
        manifest = json.loads(archive.read("manifest.json"))
    Draft202012Validator(PACKAGE_ARCHIVE_MANIFEST_SCHEMA).validate(manifest)
    invalid = deepcopy(manifest)
    invalid["packages"][0]["invented"] = True
    assert list(Draft202012Validator(PACKAGE_ARCHIVE_MANIFEST_SCHEMA).iter_errors(invalid))


def test_existing_content_declarations_match_schema_and_parser() -> None:
    path = (
        Path(__file__).parents[1]
        / "tests"
        / "fixtures"
        / "rulesets"
        / "signal-and-static"
        / "content"
        / "content-declarations.json"
    )
    # When integrated under repository tests/, parents[1] is the repository root.
    if not path.exists():
        path = (
            Path.cwd()
            / "tests"
            / "fixtures"
            / "rulesets"
            / "signal-and-static"
            / "content"
            / "content-declarations.json"
        )
    raw = path.read_bytes()
    value = json.loads(raw)
    Draft202012Validator(CONTENT_DECLARATIONS_SCHEMA).validate(value)
    assert parse_content_declarations_bytes(raw).scope_id
    value["unsupported"] = True
    assert list(Draft202012Validator(CONTENT_DECLARATIONS_SCHEMA).iter_errors(value))


@pytest.mark.parametrize("include_thinking", [False, True])
def test_actual_portable_export_manifest_matches_retrievable_definition(
    tmp_path: Path,
    include_thinking: bool,
) -> None:
    target = tmp_path / "synthetic.sylvia-gm-campaign"
    with AuthorityOwner.open(tmp_path / "owner.sqlite3", "definition-export") as owner:
        campaign_id = _campaign(owner, campaign_id="definition-campaign")
        owner.complete_turn(campaign_id, f"turn-{campaign_id}")
        _trigger_campaign_schema(owner, campaign_id)
        owner.export_campaign(
            operation_id="definition-export",
            campaign_id=campaign_id,
            target=target,
            include_thinking=include_thinking,
        )
    with zipfile.ZipFile(target) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    name = "portable_campaign_export_v2" if include_thinking else "portable_campaign_export"
    schema = get_content_definition(name)["schema"]
    assert manifest["format_version"] == (2 if include_thinking else 1)
    Draft202012Validator(schema).validate(manifest)
    invalid = deepcopy(manifest)
    invalid["table_row_counts"]["invented_table"] = 0
    assert list(Draft202012Validator(schema).iter_errors(invalid))


def test_portable_definitions_match_the_existing_codec() -> None:
    versions = [
        campaign_archive.PREDECESSOR_SOURCE_AUTHORITY_SCHEMA_VERSION,
        campaign_archive.PRE_REASONING_SOURCE_AUTHORITY_SCHEMA_VERSION,
        campaign_archive.PRE_EFFORT_SOURCE_AUTHORITY_SCHEMA_VERSION,
        campaign_archive.SOURCE_AUTHORITY_SCHEMA_VERSION,
    ]
    for name, format_version in (
        ("portable_campaign_export", 1),
        ("portable_campaign_export_v2", 2),
    ):
        definition = get_content_definition(name)
        schema = definition["schema"]
        assert schema["properties"]["source_authority_schema_version"]["enum"] == versions
        assert schema["properties"]["format_version"] == {"const": format_version}
        assert set(schema["required"]) == set(campaign_archive.MANIFEST_FIELDS)
        assert (
            tuple(schema["properties"]["table_row_counts"]["properties"])
            == campaign_archive.TABLES_IN_ORDER
        )
        for version in versions:
            example = {**definition["example"], "source_authority_schema_version": version}
            Draft202012Validator(schema).validate(example)
        assert list(
            Draft202012Validator(schema).iter_errors(
                {**definition["example"], "format_version": 3},
            )
        )
        mismatched = deepcopy(definition["example"])
        if format_version == 1:
            mismatched["non_manifest_members"].append(
                {"name": "provider-private.jsonl", "byte_count": 0, "sha256": "2" * 64},
            )
        else:
            mismatched["non_manifest_members"] = [
                row
                for row in mismatched["non_manifest_members"]
                if row["name"] != "provider-private.jsonl"
            ]
        assert list(Draft202012Validator(schema).iter_errors(mismatched))
