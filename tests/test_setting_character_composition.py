"""Representative composition of Setting world data with Ruleset character mechanics."""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

import pytest

from sylvia_gm.agent._builder_session import _participant_draft_check_tool
from sylvia_gm.agent._content_definition_tool import content_definition_tool
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.campaign_plan_sources import campaign_plan_sources
from sylvia_gm.application.reusable_participants import (
    load_reusable_participant,
    parse_setting_character,
    save_reusable_participant,
)
from sylvia_gm.builder._package_materializer import materialize_ruleset_draft
from sylvia_gm.builder._setting_materializer import materialize_setting_draft
from sylvia_gm.domain.campaign_plan import PackageBinding
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import campaign_setting_entries, canonical_json_bytes
from tests.test_character_creation_contract import _draft

WORLD_REQUIREMENTS = {
    "format_id": "sylvia-gm-setting-character-requirements",
    "format_version": 1,
    "schema": {
        "type": "object",
        "properties": {"home": {"type": "string", "minLength": 1}},
        "required": ["home"],
        "additionalProperties": False,
    },
}


def _install_setting(tmp_path: Path) -> tuple[Path, str]:
    """Publish one exact Setting closure containing a neutral calculated-state Ruleset."""

    workspace = tmp_path / "workspace"
    _draft(workspace)
    database = tmp_path / "owner.sqlite3"
    with materialize_ruleset_draft(workspace) as materialized:
        rules_archive = materialized.archive.read()
    setting_files = workspace / "setting-draft" / "files"
    setting_files.mkdir(parents=True)
    (setting_files / "world.md").write_text("# Glass Harbor\n", encoding="utf-8")
    (setting_files / "character-requirements.json").write_bytes(
        canonical_json_bytes(WORLD_REQUIREMENTS)
    )
    with AuthorityOwner.open(database, "composition-source") as owner:
        rules = owner.install_package_archive(BytesIO(rules_archive), install_id="coin-rules")
        with materialize_setting_draft(
            owner, workspace, candidate_id=rules.candidate_id,
            package_id="example.glass-harbor", version="1.0.0", title="Glass Harbor",
        ) as materialized:
            setting = owner.install_package_archive(materialized.archive, install_id="glass-harbor")
    return database, setting.candidate_id


def test_setting_and_ruleset_character_requirements_compose_and_survive_restart(
    tmp_path: Path,
) -> None:
    database, candidate_id = _install_setting(tmp_path)
    with AuthorityOwner.open(database, "composition-check") as owner:
        candidate = owner.package_candidate(candidate_id)
        sources = campaign_plan_sources(owner, candidate.package_lock)
        assert sources is not None
        assert sources.setting_character_schema == WORLD_REQUIREMENTS["schema"]
        (declaration,) = owner.package_participant_declarations(candidate_id)
        definition = content_definition_tool(
            (declaration,), sources.setting_character_schema
        ).handler({"name": "character_creation"}, None)["definition"]
        assert definition["setting_data"] == WORLD_REQUIREMENTS["schema"]
        package = candidate.package_lock.entries[0]
        setting_entry = campaign_setting_entries(candidate.package_lock)[0]
        setting = PackageBinding(
            setting_entry.package_id, setting_entry.version, setting_entry.sha256
        )
        tool = _participant_draft_check_tool(
            "companion", (declaration,), sources.setting_character_schema
        )

        missing_world = tool.handler(
            {
                "summary": "Signal keeper.",
                "authored_profile": "A keeper from the harbor.",
                "participant": {"name": "Vela", "rank": 3},
                "entity_state": "{}",
                "setting_data": "{}",
            },
            None,
        )
        assert missing_world["code"] == "setting_character_data_schema_invalid"
        assert missing_world["validation_errors"][0]["path"] == "setting_data.home"

        bad_mechanics = tool.handler(
            {
                "summary": "Signal keeper.",
                "authored_profile": "A keeper from the harbor.",
                "participant": {"name": "Vela", "rank": "three"},
                "entity_state": "{}",
                "setting_data": json.dumps({"home": "Glass Harbor"}),
            },
            None,
        )
        assert bad_mechanics["code"] == "participant_artifact_invalid"

        valid = tool.handler(
            {
                "summary": "Signal keeper.",
                "authored_profile": "A keeper from the harbor.",
                "participant": {"name": "Vela", "rank": 3},
                "entity_state": "{}",
                "setting_data": json.dumps({"home": "Glass Harbor"}),
            },
            None,
        )
        assert valid["complete_entity_state"] == {"charge": 6}
        assert valid["setting_data"] == {"home": "Glass Harbor"}
        saved = save_reusable_participant(
            tmp_path,
            operation_id="setting-character",
            role="companion",
            summary="Signal keeper.",
            authored_profile="A keeper from the harbor.",
            participant={"name": "Vela", "rank": 3},
            entity_state={},
            setting_data={"home": "Glass Harbor"},
            setting_character_schema=sources.setting_character_schema,
            package=package,
            declaration=declaration,
            campaign_setting=setting,
        )
        artifact_id = saved.artifact_id
        assert saved.setting_data == {"home": "Glass Harbor"}
        character_source = {
            "format_id": "sylvia-gm-character-file", "format_version": 2,
            "id": "vela", "schema": {
                "id": declaration.schema_id, "content_sha256": declaration.schema_sha256,
            },
            "summary": saved.summary, "authored_profile": saved.authored_profile,
            "participant": saved.participant, "entity_state": saved.entity_state,
            "setting_data": saved.setting_data,
        }
        static = parse_setting_character(
            canonical_json_bytes(character_source).decode(), character_id="vela", role="companion",
            package=package, declaration=declaration, campaign_setting=setting,
            setting_character_schema=sources.setting_character_schema,
        )
        assert static.setting_data == saved.setting_data
        character_source["setting_data"] = {}
        with pytest.raises(CommandRejected) as missing_static:
            parse_setting_character(
                canonical_json_bytes(character_source).decode(),
                character_id="vela", role="companion", package=package,
                declaration=declaration, campaign_setting=setting,
                setting_character_schema=sources.setting_character_schema,
            )
        assert missing_static.value.diagnostics[0].path == "setting_data.home"

    with AuthorityOwner.open(database, "composition-reopened") as owner:
        candidate = owner.package_candidate(candidate_id)
        sources = campaign_plan_sources(owner, candidate.package_lock)
        assert sources is not None
        (declaration,) = owner.package_participant_declarations(candidate_id)
        package = candidate.package_lock.entries[0]
        setting_entry = campaign_setting_entries(candidate.package_lock)[0]
        restored = load_reusable_participant(
            tmp_path,
            artifact_id,
            package=package,
            declaration=declaration,
            campaign_setting=PackageBinding(
                setting_entry.package_id, setting_entry.version, setting_entry.sha256
            ),
            setting_character_schema=sources.setting_character_schema,
        )
        assert restored.entity_state == {"charge": 6}
        assert restored.setting_data == {"home": "Glass Harbor"}


def test_invalid_setting_requirements_reject_before_candidate_publication(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _draft(workspace)
    with materialize_ruleset_draft(workspace) as materialized:
        rules_archive = materialized.archive.read()
    files = workspace / "setting-draft" / "files"
    files.mkdir(parents=True)
    invalid = dict(WORLD_REQUIREMENTS)
    invalid["schema"] = {
        "type": "object",
        "properties": {"home": {"type": "string"}},
        "required": ["home"],
        "patternProperties": {},
        "additionalProperties": False,
    }
    (files / "character-requirements.json").write_bytes(canonical_json_bytes(invalid))
    database = tmp_path / "invalid.sqlite3"
    with AuthorityOwner.open(database, "invalid-setting") as owner:
        rules = owner.install_package_archive(BytesIO(rules_archive), install_id="coin-rules")
        with materialize_setting_draft(
            owner, workspace, candidate_id=rules.candidate_id,
            package_id="example.invalid-world", version="1.0.0", title="Invalid World",
        ) as materialized, pytest.raises(CommandRejected):
            owner.install_package_archive(materialized.archive, install_id="invalid-world")
        assert owner.package_candidates() == (rules,)
