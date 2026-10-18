from __future__ import annotations

from pathlib import Path

import pytest

from sylvia_gm.application.compatible_character_copy import copy_compatible_character
from sylvia_gm.application.reusable_participants import (
    load_reusable_participant,
    save_reusable_participant,
)
from sylvia_gm.domain.authority import PackageLockEntry, PackageRole
from sylvia_gm.domain.campaign_plan import PackageBinding
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import (
    ParticipantArtifactDeclaration,
    participant_schema_sha256,
)

PARTICIPANT_SCHEMA = {
    "$id": "example.character",
    "type": "object",
    "properties": {"name": {"type": "string"}, "rank": {"type": "integer"}},
    "required": ["name", "rank"],
    "additionalProperties": False,
}
STATE_SCHEMA = {
    "type": "object",
    "properties": {"base": {"type": "integer"}, "charge": {"type": "integer"}},
    "required": ["base", "charge"],
    "additionalProperties": False,
}
WORLD_SCHEMA = {
    "type": "object",
    "properties": {"home": {"type": "string"}},
    "required": ["home"],
    "additionalProperties": False,
}


def declaration(
    expression: str = "state.base + participant.rank",
) -> ParticipantArtifactDeclaration:
    calculations = ({"set": {"field": "charge", "value": expression}},)
    return ParticipantArtifactDeclaration(
        kind="companion",
        schema_id="example.character",
        schema_sha256=participant_schema_sha256(
            PARTICIPANT_SCHEMA,
            entity_state_schema=STATE_SCHEMA,
            initial_state_calculations=calculations,
        ),
        schema=PARTICIPANT_SCHEMA,
        entity_state_schema=STATE_SCHEMA,
        initial_state_calculations=calculations,
    )


def package(version: str, digit: str) -> PackageLockEntry:
    return PackageLockEntry(
        package_id="example.rules",
        version=version,
        sha256=digit * 64,
        role=PackageRole.PRIMARY,
        load_order=0,
        dependencies=(),
        capabilities=(),
    )


def setting(version: str, digit: str) -> PackageBinding:
    return PackageBinding("example.world", version, digit * 64)


def saved_source(tmp_path: Path):
    return save_reusable_participant(
        tmp_path,
        operation_id="original",
        role="companion",
        summary="Signal keeper.",
        authored_profile="# Vela\n\nA signal keeper.",
        participant={"name": "Vela", "rank": 3},
        entity_state={"base": 4},
        setting_data={"home": "Glass Harbor"},
        package=package("1.0.0", "1"),
        declaration=declaration(),
        campaign_setting=setting("1.0.0", "2"),
        setting_character_schema=WORLD_SCHEMA,
    )


def test_unchanged_character_gets_new_exact_binding_and_survives_reload(tmp_path: Path) -> None:
    source = saved_source(tmp_path)
    original_root = tmp_path / "participants" / source.artifact_id
    original_files = {item.name: item.read_bytes() for item in original_root.iterdir()}
    target_package = package("1.1.0", "3")
    target_setting = setting("2.0.0", "4")

    copied = copy_compatible_character(
        tmp_path,
        source=source,
        expected_content_sha256=source.content_sha256,
        operation_id="explicit-compatible-copy",
        package=target_package,
        declaration=declaration(),
        campaign_setting=target_setting,
        setting_character_schema=WORLD_SCHEMA,
    )

    assert copied.artifact_id != source.artifact_id
    assert copied.participant == source.participant
    assert copied.entity_state == source.entity_state == {"base": 4, "charge": 7}
    assert copied.setting_data == source.setting_data
    assert copied.campaign_setting_sha256 == "4" * 64
    assert {item.name: item.read_bytes() for item in original_root.iterdir()} == original_files
    assert (
        copy_compatible_character(
            tmp_path,
            source=source,
            expected_content_sha256=source.content_sha256,
            operation_id="explicit-compatible-copy",
            package=target_package,
            declaration=declaration(),
            campaign_setting=target_setting,
            setting_character_schema=WORLD_SCHEMA,
        )
        == copied
    )
    assert (
        load_reusable_participant(
            tmp_path,
            copied.artifact_id,
            package=target_package,
            declaration=declaration(),
            campaign_setting=target_setting,
            setting_character_schema=WORLD_SCHEMA,
        )
        == copied
    )


@pytest.mark.parametrize("incompatibility", ["new_world_field", "changed_formula"])
def test_incompatible_destination_requires_adaptation_without_writes(
    tmp_path: Path,
    incompatibility: str,
) -> None:
    source = saved_source(tmp_path)
    before = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    target_world = WORLD_SCHEMA
    target_declaration = declaration()
    if incompatibility == "new_world_field":
        target_world = {
            "type": "object",
            "properties": {"home": {"type": "string"}, "faction": {"type": "string"}},
            "required": ["home", "faction"],
            "additionalProperties": False,
        }
    else:
        target_declaration = declaration("state.base + participant.rank + 1")

    with pytest.raises(CommandRejected) as rejected:
        copy_compatible_character(
            tmp_path,
            source=source,
            expected_content_sha256=source.content_sha256,
            operation_id=f"rejected-{incompatibility}",
            package=package("2.0.0", "5"),
            declaration=target_declaration,
            campaign_setting=setting("2.0.0", "6"),
            setting_character_schema=target_world,
        )
    assert rejected.value.code == "character_adaptation_required"
    assert rejected.value.diagnostics
    assert {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    } == before
