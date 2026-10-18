"""Compatible reuse must retain the same read-only rule context as creation/load."""

from pathlib import Path

import pytest

from sylvia_gm.application.compatible_character_copy import copy_compatible_character
from sylvia_gm.application.reusable_participants import save_reusable_participant
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import (
    ParticipantArtifactDeclaration,
    participant_schema_sha256,
)
from tests.test_compatible_character_copy import (
    PARTICIPANT_SCHEMA,
    STATE_SCHEMA,
    package,
    setting,
)


def rule_declaration(minimum_rank: int = 0) -> ParticipantArtifactDeclaration:
    calculations = ({"set": {"field": "charge", "value": "rules._computed_charge"}},)
    validation = {
        "description": "Derive charge from validated authored choices.",
        "definitions": {},
        "effects": [
            {
                "require": {
                    "condition": f"participant.rank >= {minimum_rank}",
                    "error": "The destination requires a higher rank.",
                }
            },
            {
                "set": {
                    "field": "_computed_charge",
                    "value": "state.base + participant.rank",
                }
            },
        ],
    }
    return ParticipantArtifactDeclaration(
        kind="companion",
        schema_id="example.character",
        schema_sha256=participant_schema_sha256(
            PARTICIPANT_SCHEMA,
            entity_state_schema=STATE_SCHEMA,
            initial_state_calculations=calculations,
            initial_state_validation=validation,
        ),
        schema=PARTICIPANT_SCHEMA,
        entity_state_schema=STATE_SCHEMA,
        initial_state_calculations=calculations,
        initial_state_validation=validation,
    )


def test_compatible_copy_keeps_rules_used_by_initial_calculations(tmp_path: Path) -> None:
    declared = rule_declaration()
    source = save_reusable_participant(
        tmp_path,
        operation_id="rule-source",
        role="companion",
        summary="Vela.",
        authored_profile="# Vela\n\nAn archivist.",
        participant={"name": "Vela", "rank": 3},
        entity_state={"base": 4},
        package=package("1.0.0", "1"),
        declaration=declared,
        campaign_setting=setting("1.0.0", "2"),
    )
    old = tmp_path / "participants" / source.artifact_id
    before = {p.name: p.read_bytes() for p in old.iterdir()}
    copied = copy_compatible_character(
        tmp_path,
        source=source,
        expected_content_sha256=source.content_sha256,
        operation_id="rule-copy",
        package=package("1.1.0", "3"),
        declaration=declared,
        campaign_setting=setting("1.1.0", "4"),
    )
    assert copied.artifact_id != source.artifact_id
    assert copied.entity_state == source.entity_state == {"base": 4, "charge": 7}
    assert {p.name: p.read_bytes() for p in old.iterdir()} == before


def test_incompatible_rules_reject_at_the_destination_rule_without_saving(tmp_path: Path) -> None:
    source = save_reusable_participant(
        tmp_path,
        operation_id="rule-source",
        role="companion",
        summary="Vela.",
        authored_profile="# Vela\n\nAn archivist.",
        participant={"name": "Vela", "rank": 3},
        entity_state={"base": 4},
        package=package("1.0.0", "1"),
        declaration=rule_declaration(),
        campaign_setting=setting("1.0.0", "2"),
    )
    before = {p.name for p in (tmp_path / "participants").iterdir()}
    with pytest.raises(CommandRejected) as caught:
        copy_compatible_character(
            tmp_path,
            source=source,
            expected_content_sha256=source.content_sha256,
            operation_id="forbidden-copy",
            package=package("1.1.0", "3"),
            declaration=rule_declaration(minimum_rank=4),
            campaign_setting=setting("1.1.0", "4"),
        )
    assert caught.value.code == "character_adaptation_required"
    assert any(item.path == "initial_state_validation" for item in caught.value.diagnostics)
    assert {p.name for p in (tmp_path / "participants").iterdir()} == before
