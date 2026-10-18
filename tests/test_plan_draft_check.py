"""A Plan check uses its edited contained files and does not publish any draft content."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sylvia_gm.agent._builder_session import _campaign_plan_draft_tool
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.campaign_plan_sources import campaign_plan_sources
from sylvia_gm.builder._plan_draft_check import check_plan_draft
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import canonical_json_bytes
from tests.test_setting_character_composition import _install_setting


def test_plan_check_uses_local_character_and_requirement_edits_without_publication(
    tmp_path: Path,
) -> None:
    database, candidate_id = _install_setting(tmp_path)
    workspace = tmp_path / "plan-workspace"
    with AuthorityOwner.open(database, "plan-draft-check") as owner:
        source = owner.package_candidate(candidate_id)
        authority = campaign_plan_sources(owner, source.package_lock)
        assert authority is not None
        created = _campaign_plan_draft_tool(workspace, authority).handler(
            {
                "title": "Relay Expedition",
                "premise": "Restore the relay.",
                "tone": "Hopeful",
                "player_character_mode": "compatible",
                "starting_location_path": "world.md",
                "opening_situation": "The keeper asks for help.",
                "primary_objective": "Reach the silent relay.",
                "completion_conditions": ["The signal is disabled or the relay abandoned, and the keeper has responded to that outcome."],
                "custom_companion_minimum": 0,
                "custom_companion_maximum": 1,
            },
            None,
        )
        assert created["success"] is True, created
        plan_path = workspace / "campaign-plan-draft" / "plan.json"
        plan = json.loads(plan_path.read_bytes())
        plan["starting_party"]["required_companions"] = [
            {"id": "vela", "path": "characters/vela.json"}
        ]
        plan_path.write_bytes(canonical_json_bytes(plan))
        (declaration,) = authority.character_declarations
        character = {
            "format_id": "sylvia-gm-character-file",
            "format_version": 2,
            "id": "vela",
            "schema": {"id": declaration.schema_id, "content_sha256": declaration.schema_sha256},
            "summary": "Signal keeper.",
            "authored_profile": "# Vela\n\nThe harbor's keeper.",
            "participant": {"name": "Vela", "rank": 3},
            "entity_state": {"charge": 6},
            "setting_data": {"home": "Glass Harbor"},
        }
        files = workspace / "campaign-plan-draft" / "setting-files"
        (files / "characters").mkdir(parents=True)
        (files / "characters" / "vela.json").write_bytes(canonical_json_bytes(character))
        declarations_path = "content/content-declarations.json"
        content = json.loads(authority.read_setting(declarations_path))
        content["inclusions"][0]["links"]["artifacts"].append("characters/vela.json")
        content["inclusions"][0]["links"]["artifacts"].sort()
        (files / "content").mkdir()
        (files / declarations_path).write_bytes(canonical_json_bytes(content))
        before = {
            path.relative_to(workspace): path.read_bytes()
            for path in workspace.rglob("*")
            if path.is_file()
        }
        candidates = owner.package_candidates()
        checked = check_plan_draft(
            owner, workspace, candidate_id=candidate_id, expected_authority=authority
        )
        assert checked["published"] is False
        assert checked["required_characters_ready"] is True
        assert checked["closure_sha256"] != source.closure_sha256
        assert owner.package_candidates() == candidates
        assert {
            path.relative_to(workspace): path.read_bytes()
            for path in workspace.rglob("*")
            if path.is_file()
        } == before

        changed_requirements = {
            "format_id": "sylvia-gm-setting-character-requirements",
            "format_version": 1,
            "schema": {
                "type": "object",
                "properties": {
                    "home": {"type": "string"},
                    "faction": {"type": "string"},
                },
                "required": ["home", "faction"],
                "additionalProperties": False,
            },
        }
        (files / "character-requirements.json").write_bytes(
            canonical_json_bytes(changed_requirements)
        )
        incomplete = check_plan_draft(
            owner, workspace, candidate_id=candidate_id, expected_authority=authority
        )
        assert incomplete["required_characters_ready"] is False
        assert incomplete["character_validation_errors"][0]["path"] == "setting_data.faction"
        with pytest.raises(CommandRejected):
            check_plan_draft(
                owner,
                workspace,
                candidate_id=candidate_id,
                expected_authority=authority,
                require_complete_characters=True,
            )
        assert owner.package_candidates() == candidates
        assert not (tmp_path / "campaign-plans").exists()
