from __future__ import annotations

import json

import pytest

from sylvia_gm.application.setting_character_requirements import (
    parse_setting_character_requirements,
    validate_setting_character_data,
)
from sylvia_gm.domain.errors import CommandRejected


def source(schema: object, *, version: object = 1) -> str:
    return json.dumps(
        {
            "format_id": "sylvia-gm-setting-character-requirements",
            "format_version": version,
            "schema": schema,
        }
    )


SCHEMA = {
    "type": "object",
    "properties": {
        "home": {"type": "string", "minLength": 1},
        "affiliation": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": {"type": "string"},
        },
    },
    "required": ["home"],
    "additionalProperties": False,
}


def test_valid_world_information_and_declared_nested_extensions() -> None:
    schema = parse_setting_character_requirements(source(SCHEMA))
    data = {"home": "Glass Harbor", "affiliation": {"name": "Pilots", "rank": "junior"}}
    assert validate_setting_character_data(data, schema=schema) == data
    assert validate_setting_character_data({}, schema=None) == {}


@pytest.mark.parametrize(
    ("data", "path"),
    [
        ({}, "setting_data.home"),
        ({"home": "Glass Harbor", "weather": "rain"}, "setting_data.weather"),
        ({"home": 7}, "setting_data.home"),
    ],
)
def test_required_undeclared_and_wrong_type_world_fields_reject(data: object, path: str) -> None:
    schema = parse_setting_character_requirements(source(SCHEMA))
    with pytest.raises(CommandRejected) as rejected:
        validate_setting_character_data(data, schema=schema)
    assert path in [diagnostic.path for diagnostic in rejected.value.diagnostics]
    assert "Glass Harbor" not in str(rejected.value.diagnostics)


def test_missing_file_contract_allows_only_empty_setting_data() -> None:
    with pytest.raises(CommandRejected) as rejected:
        validate_setting_character_data({"home": "undeclared"}, schema=None)
    assert rejected.value.diagnostics[0].path == "setting_data"


@pytest.mark.parametrize(
    "bad_source",
    [
        '{"format_id":"sylvia-gm-setting-character-requirements","format_version":1,'
        '"schema":{"type":"object","additionalProperties":false},"schema":{}}',
        source(SCHEMA, version=True),
        source({"type": "object", "additionalProperties": False, "patternProperties": {}}),
        source(
            {
                "type": "object",
                "properties": {"nested": {"type": "object"}},
                "additionalProperties": False,
            }
        ),
        source({"type": "string"}),
    ],
)
def test_duplicate_bool_version_and_unsupported_schemas_reject(bad_source: str) -> None:
    with pytest.raises(CommandRejected):
        parse_setting_character_requirements(bad_source)
