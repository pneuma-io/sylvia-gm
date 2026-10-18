"""Focused checks for the shared Character File definition and semantic parser owner."""

from __future__ import annotations

import json

import pytest

from sylvia_gm.application.reusable_participants import parse_setting_character
from sylvia_gm.domain.authority import PackageLockEntry, PackageRole
from sylvia_gm.domain.campaign_plan import PackageBinding
from sylvia_gm.domain.content_definitions import get_content_definition
from sylvia_gm.domain.content_validation import ContentShapeError, validate_content_shape
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import (
    ParticipantArtifactDeclaration,
    canonical_json_bytes,
    participant_schema_sha256,
)

PARTICIPANT_SCHEMA = {
    "$id": "example.signal-rules.participant",
    "type": "object",
    "properties": {"name": {"type": "string"}, "rank": {"type": "integer"}},
    "required": ["name", "rank"],
    "additionalProperties": False,
}
ENTITY_STATE_SCHEMA = {
    "type": "object",
    "properties": {"charge": {"type": "integer"}},
    "required": ["charge"],
    "additionalProperties": False,
}


def _authority() -> tuple[PackageLockEntry, ParticipantArtifactDeclaration, PackageBinding]:
    declaration = ParticipantArtifactDeclaration(
        kind="companion",
        schema_id="example.signal-rules.participant",
        schema_sha256=participant_schema_sha256(
            PARTICIPANT_SCHEMA, entity_state_schema=ENTITY_STATE_SCHEMA
        ),
        schema=PARTICIPANT_SCHEMA,
        entity_state_schema=ENTITY_STATE_SCHEMA,
    )
    package = PackageLockEntry(
        package_id="example.signal-rules",
        version="1.0.0",
        sha256="2" * 64,
        role=PackageRole.PRIMARY,
        load_order=0,
        dependencies=(),
        capabilities=(),
    )
    setting = PackageBinding("example.signal-setting", "1.0.0", "3" * 64)
    return package, declaration, setting


def _character() -> dict[str, object]:
    _package, declaration, _setting = _authority()
    return {
        "format_id": "sylvia-gm-character-file",
        "format_version": 2,
        "id": "relay-keeper",
        "schema": {
            "id": declaration.schema_id,
            "content_sha256": declaration.schema_sha256,
        },
        "summary": "A relay keeper who can join the expedition.",
        "authored_profile": "# Relay Keeper\n\nThe keeper records every unanswered call.",
        "participant": {"name": "Relay Keeper", "rank": 2},
        "entity_state": {"charge": 4},
        "setting_data": {"home": "North Relay"},
    }


def test_character_file_definition_example_has_the_current_closed_shape() -> None:
    definition = get_content_definition("character_file")

    validate_content_shape("character_file", definition["example"])
    assert definition["schema"]["properties"]["format_version"]["const"] == 2
    assert set(definition["example"]) == set(definition["schema"]["required"])


@pytest.mark.parametrize(
    "change,path",
    [
        (lambda value: value.update(unexpected=True), "$.unexpected"),
        (lambda value: value.update(participant=[]), "$.participant"),
        (lambda value: value.update(entity_state=[]), "$.entity_state"),
        (lambda value: value.update(setting_data=[]), "$.setting_data"),
        (lambda value: value.update(summary="Trailing newline\n"), "$.summary"),
        (lambda value: value.update(authored_profile="Trailing newline\n"), "$.authored_profile"),
    ],
)
def test_character_file_shape_rejects_root_and_context_container_errors(change, path) -> None:
    value = _character()
    change(value)

    with pytest.raises(ContentShapeError) as rejected:
        validate_content_shape("character_file", value, file="characters/relay-keeper.json")

    assert rejected.value.diagnostics[0].path == path


def test_character_file_semantics_use_exact_ruleset_and_setting_declarations() -> None:
    package, declaration, setting = _authority()
    setting_schema = {
        "type": "object",
        "properties": {"home": {"type": "string", "minLength": 1}},
        "required": ["home"],
        "additionalProperties": False,
    }
    value = _character()

    parsed = parse_setting_character(
        canonical_json_bytes(value).decode(),
        character_id="relay-keeper",
        role="companion",
        package=package,
        declaration=declaration,
        campaign_setting=setting,
        setting_character_schema=setting_schema,
    )

    assert parsed.participant == value["participant"]
    assert parsed.entity_state == value["entity_state"]
    assert parsed.setting_data == value["setting_data"]

    value["setting_data"] = {"world": "undeclared"}
    with pytest.raises(CommandRejected):
        parse_setting_character(
            json.dumps(value),
            character_id="relay-keeper",
            role="companion",
            package=package,
            declaration=declaration,
            campaign_setting=setting,
            setting_character_schema=setting_schema,
        )
