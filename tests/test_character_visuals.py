"""Both canonical references are actual character data, preserved through supported formats."""

import sqlite3
from types import SimpleNamespace

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.reusable_participants import (
    load_reusable_participant,
    parse_setting_character,
    save_reusable_participant,
)
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.image_art import validate_character_visuals
from sylvia_gm.domain.package_library import canonical_json_bytes
from tests.test_character_content_definition import _authority, _character


def visuals():
    return {
        "appearance": "Silver hair, brown eyes, blue coat",
        "full_body": {"sha256": "a" * 64, "operation_id": "full-body"},
        "reference_sheet": {"sha256": "b" * 64, "operation_id": "sheet"},
    }


def test_character_file_and_reusable_library_preserve_both_references(tmp_path):
    package, declaration, setting = _authority()
    value = {**_character(), "visuals": visuals()}
    parsed = parse_setting_character(
        canonical_json_bytes(value).decode(),
        character_id=value["id"],
        role="companion",
        package=package,
        declaration=declaration,
        campaign_setting=setting,
        setting_character_schema={
            "type": "object",
            "properties": {"home": {"type": "string"}},
            "required": ["home"],
            "additionalProperties": False,
        },
    )
    assert parsed.visuals == visuals()
    saved = save_reusable_participant(
        tmp_path,
        operation_id="with-visuals",
        role="companion",
        summary=parsed.summary,
        authored_profile=parsed.authored_profile,
        participant=parsed.participant,
        entity_state=parsed.entity_state,
        package=package,
        declaration=declaration,
        campaign_setting=setting,
        visuals=visuals(),
    )
    reopened = load_reusable_participant(
        tmp_path,
        saved.artifact_id,
        package=package,
        declaration=declaration,
        campaign_setting=setting,
    )
    assert reopened.visuals == visuals()
    assert reopened.rift_source()["visuals"] == visuals()


def test_visual_bundle_requires_both_reference_formats():
    value = visuals()
    del value["reference_sheet"]
    with pytest.raises(ValueError, match="both"):
        validate_character_visuals(value)


def test_existing_schema_12_profile_migrates_with_saved_chat_intact(tmp_path):
    database = tmp_path / "legacy.db"
    with AuthorityOwner.open(database, "original") as owner:
        owner.create_builder_chat(provider_id="test", model_id="test", session_id="kept-chat")
        owner.prepare_new_campaign_storage()
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE image_selections")
        connection.execute("DROP TABLE image_requests")
        connection.execute("DELETE FROM schema_migrations WHERE version=13")
        connection.execute("PRAGMA user_version=12")
    with AuthorityOwner.open(database, "migrated") as owner:
        assert owner.session_context("kept-chat").session.session_id == "kept-chat"
        owner._store.images.interrupt_pending()
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 13


def test_prepared_character_visuals_are_read_from_immutable_construction_arrays(
    tmp_path, monkeypatch
):
    with AuthorityOwner.open(tmp_path / "prepared.db", "owner") as owner:
        construction = SimpleNamespace(
            canonical_input=owned_payload(
                {
                    "selected_party": [
                        {
                            "participant_id": "keeper",
                            "character_file": {"visuals": visuals()},
                        }
                    ]
                }
            )
        )
        monkeypatch.setattr(owner._store, "campaign_construction_basis", lambda _: construction)
        assert owner.character_visuals("prepared-campaign")["keeper"] == visuals()
