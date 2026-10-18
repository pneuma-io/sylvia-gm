from __future__ import annotations

import copy
import io
import json
import zipfile

import pytest
from jsonschema import Draft202012Validator

from sylvia_gm.domain._content_schema_package_subformats import (
    CAMPAIGN_SETTING_KNOWLEDGE_EXAMPLE,
    CAMPAIGN_SETTING_KNOWLEDGE_SCHEMA,
    GAMEPLAY_SUPPORT_EXAMPLE,
    GAMEPLAY_SUPPORT_SCHEMA,
    PORTABLE_PLAN_BUNDLE_RECORD_SCHEMA,
)
from sylvia_gm.domain.package_library import (
    canonical_json_bytes,
    parse_campaign_setting_knowledge_bytes,
    parse_gameplay_support_bytes,
)


def test_package_subformat_examples_match_their_existing_parsers() -> None:
    Draft202012Validator(CAMPAIGN_SETTING_KNOWLEDGE_SCHEMA).validate(
        CAMPAIGN_SETTING_KNOWLEDGE_EXAMPLE
    )
    parsed_knowledge = parse_campaign_setting_knowledge_bytes(
        canonical_json_bytes(CAMPAIGN_SETTING_KNOWLEDGE_EXAMPLE)
    )
    assert parsed_knowledge.facts[0].identity == "relay-purpose"

    Draft202012Validator(GAMEPLAY_SUPPORT_SCHEMA).validate(GAMEPLAY_SUPPORT_EXAMPLE)
    parsed_support = parse_gameplay_support_bytes(canonical_json_bytes(GAMEPLAY_SUPPORT_EXAMPLE))
    assert parsed_support.records[0].gameplay_resolution == "hybrid"


@pytest.mark.parametrize(
    ("schema", "example", "mutate"),
    [
        (
            CAMPAIGN_SETTING_KNOWLEDGE_SCHEMA,
            CAMPAIGN_SETTING_KNOWLEDGE_EXAMPLE,
            lambda value: value["facts"][0].update(extra=True),
        ),
        (
            GAMEPLAY_SUPPORT_SCHEMA,
            GAMEPLAY_SUPPORT_EXAMPLE,
            lambda value: value["records"][0].update(gm_part=None),
        ),
    ],
)
def test_package_subformat_schemas_reject_closed_or_conditional_errors(
    schema,
    example,
    mutate,
) -> None:
    invalid = copy.deepcopy(example)
    mutate(invalid)
    assert list(Draft202012Validator(schema).iter_errors(invalid))


def test_exported_synthetic_plan_record_matches_portable_bundle_definition(tmp_path) -> None:
    # Reuse the existing authentic synthetic export journey rather than fabricating bundle hashes.
    from tests.test_plan_bundles import portable_plan

    _plan, _source, archive = portable_plan(tmp_path / "source")
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        record_bytes = bundle.read("record.json")
        record = json.loads(record_bytes)
        assert record_bytes == canonical_json_bytes(record)
        assert set(bundle.namelist()) >= {
            "record.json",
            "plan.json",
            "plan.md",
            "setting.sylvia-gm-package",
        }
    Draft202012Validator(PORTABLE_PLAN_BUNDLE_RECORD_SCHEMA).validate(record)


def test_portable_plan_record_rejects_legacy_version_and_unknown_field() -> None:
    # The registry definition is specifically the portable v3 bundle record.
    from sylvia_gm.domain._content_schema_package_subformats import (
        PORTABLE_PLAN_BUNDLE_RECORD_EXAMPLE,
    )

    legacy = {**PORTABLE_PLAN_BUNDLE_RECORD_EXAMPLE, "format_version": 2}
    unknown = {**PORTABLE_PLAN_BUNDLE_RECORD_EXAMPLE, "invented": True}
    validator = Draft202012Validator(PORTABLE_PLAN_BUNDLE_RECORD_SCHEMA)
    assert list(validator.iter_errors(legacy))
    assert list(validator.iter_errors(unknown))
